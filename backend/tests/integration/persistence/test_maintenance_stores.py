"""Adaptadores SQL de los trabajos periódicos contra PostgreSQL real, incluidos los
privilegios mínimos de `nave_app` y `nave_maintenance` (AM-305)."""

import threading
from collections.abc import Iterator, Sequence
from datetime import datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import Engine, event, func, insert, select, text, update
from sqlalchemy.engine import URL
from sqlalchemy.exc import DBAPIError

from nave.application.dto.maintenance import OutboxMessage, SweepThresholds
from nave.application.ports.messaging import ScanStep
from nave.application.use_cases.abandon_scan import AbandonScan
from nave.application.use_cases.relay_outbox import RelayOutbox
from nave.application.use_cases.sweep_stuck_scans import SweepStuckScans
from nave.domain.value_objects.identifiers import ScanId
from nave.domain.value_objects.scan_status import ScanStatus
from nave.domain.value_objects.status_reason import ReasonCode
from nave.infrastructure.persistence import schema as t
from nave.infrastructure.persistence.engine import create_database_engine
from nave.infrastructure.persistence.maintenance import (
    SqlOutboxStore,
    SqlRetentionStore,
    SqlScanSweepStore,
)
from nave.infrastructure.persistence.unit_of_work import SqlUnitOfWorkFactory
from tests.fakes.memory import FakeClock, RecordingQueue
from tests.integration.persistence.seed import (
    OTHER_REPO,
    REPO,
    TRIAGER,
    at,
    new_finding,
    scan_id,
    seed_tenancy,
)

pytestmark = pytest.mark.integration

NOW = at(24 * 60 * 400)  # ~400 días después de T0


def engine_as(url: URL, role: str) -> Engine:
    engine = create_database_engine(url)

    @event.listens_for(engine, "connect")
    def _set_role(dbapi_connection: Any, _record: Any) -> None:
        dbapi_connection.execute(f'SET ROLE "{role}"')
        dbapi_connection.commit()

    return engine


@pytest.fixture
def app_engine(database_url: URL, engine: Engine) -> Iterator[Engine]:
    seed_tenancy(engine)
    app = engine_as(database_url, "nave_app")
    yield app
    app.dispose()


@pytest.fixture
def maintenance_engine(database_url: URL, app_engine: Engine) -> Iterator[Engine]:
    maintenance = engine_as(database_url, "nave_maintenance")
    yield maintenance
    maintenance.dispose()


def put_scan(  # noqa: PLR0913 - constructor de datos de prueba
    engine: Engine,
    n: int,
    *,
    status: str = "FAILED",
    ref: str = "main",
    repository: Any = REPO,
    queued: datetime | None = None,
    started: datetime | None = None,
    finished: datetime | None = None,
    superseded_by: ScanId | None = None,
) -> ScanId:
    terminal = status in {"FAILED", "CANCELLED", "TIMED_OUT"}
    with engine.begin() as conn:
        conn.execute(
            insert(t.scans).values(
                id=scan_id(n).value,
                repository_id=repository.value,
                commit_sha=f"{n:040x}",
                ref=ref,
                trigger="MANUAL",
                requested_by=TRIAGER.value,
                trigger_key=f"manual:{n}",
                status=status,
                queued_at=queued or at(0),
                started_at=started,
                finished_at=finished,
                status_reason_code=ReasonCode.INTERNAL_ERROR.value if terminal else None,
                superseded_by=superseded_by.value if superseded_by else None,
                version=0,
            )
        )
    return scan_id(n)


def scan_ids(engine: Engine) -> set[int]:
    with engine.connect() as conn:
        rows = conn.execute(select(t.scans.c.id)).all()
    return {int(r.id.int) - 0x5000 for r in rows}


def put_outbox(engine: Engine, n: int, *, published: datetime | None = None) -> None:
    with engine.begin() as conn:
        conn.execute(
            insert(t.outbox_events).values(
                type="ScanQueued", payload={"n": n}, occurred_at=at(n), published_at=published
            )
        )


class TestOutboxStore:
    def test_relay_delivers_in_order_and_marks_published(self, app_engine: Engine) -> None:
        for n in range(5):
            put_outbox(app_engine, n)
        clock = FakeClock(NOW)
        got: list[Sequence[OutboxMessage]] = []
        store = SqlOutboxStore(app_engine, clock)
        assert store.relay_batch(3, got.append) == 3
        assert [m.payload["n"] for m in got[0]] == [0, 1, 2]
        assert store.relay_batch(10, got.append) == 2
        assert store.relay_batch(10, got.append) == 0
        with app_engine.connect() as conn:
            published = conn.execute(select(t.outbox_events.c.published_at)).scalars().all()
        assert set(published) == {NOW}

    def test_messages_carry_the_stored_type_payload_and_time(self, app_engine: Engine) -> None:
        put_outbox(app_engine, 7)
        got: list[Sequence[OutboxMessage]] = []
        SqlOutboxStore(app_engine, FakeClock(NOW)).relay_batch(1, got.append)
        (message,) = got[0]
        assert (message.type, dict(message.payload), message.occurred_at) == (
            "ScanQueued",
            {"n": 7},
            at(7),
        )

    def test_a_failing_delivery_publishes_nothing(self, app_engine: Engine) -> None:
        put_outbox(app_engine, 1)

        def boom(_: Sequence[OutboxMessage]) -> None:
            raise RuntimeError

        store = SqlOutboxStore(app_engine, FakeClock(NOW))
        with pytest.raises(RuntimeError):
            store.relay_batch(10, boom)
        assert store.relay_batch(10, lambda _: None) == 1

    def test_concurrent_relays_take_different_events_and_do_not_block(
        self, app_engine: Engine
    ) -> None:
        for n in range(4):
            put_outbox(app_engine, n)
        store = SqlOutboxStore(app_engine, FakeClock(NOW))
        inside, release = threading.Event(), threading.Event()
        first: list[int] = []

        def slow(messages: Sequence[OutboxMessage]) -> None:
            first.extend(m.id for m in messages)
            inside.set()
            assert release.wait(10)

        worker = threading.Thread(target=lambda: store.relay_batch(2, slow))
        worker.start()
        assert inside.wait(10)
        second: list[int] = []
        assert store.relay_batch(2, lambda ms: second.extend(m.id for m in ms)) == 2
        release.set()
        worker.join(10)
        assert not set(first) & set(second)
        assert len(first) == len(second) == 2


class TestSweepStore:
    def test_queued_scans_older_than_the_cutoff_oldest_first(self, app_engine: Engine) -> None:
        put_scan(app_engine, 1, status="QUEUED", queued=at(30))
        put_scan(app_engine, 2, status="QUEUED", queued=at(10))
        put_scan(app_engine, 3, status="QUEUED", queued=at(50))
        put_scan(app_engine, 4, status="FAILED", queued=at(1), finished=at(2))
        store = SqlScanSweepStore(app_engine)
        rows = store.queued_before(at(40), 10)
        assert [r.scan_id for r in rows] == [scan_id(2), scan_id(1)]
        assert all(r.status is ScanStatus.QUEUED and r.requeued_at is None for r in rows)
        assert [r.since for r in rows] == [at(10), at(30)]
        assert len(store.queued_before(at(40), 1)) == 1

    def test_running_scans_use_started_at_and_cover_the_three_active_states(
        self, app_engine: Engine
    ) -> None:
        for n, status in enumerate(("FETCHING", "SCANNING", "PROCESSING"), start=1):
            put_scan(app_engine, n, status=status, queued=at(0), started=at(n))
        put_scan(app_engine, 9, status="SCANNING", queued=at(0), started=at(90))
        put_scan(app_engine, 8, status="QUEUED", queued=at(0))
        rows = SqlScanSweepStore(app_engine).running_before(at(60), 10)
        assert [r.scan_id for r in rows] == [scan_id(1), scan_id(2), scan_id(3)]
        assert [r.since for r in rows] == [at(1), at(2), at(3)]

    def test_mark_requeued_only_touches_queued_scans(self, app_engine: Engine) -> None:
        queued = put_scan(app_engine, 1, status="QUEUED", queued=at(1))
        running = put_scan(app_engine, 2, status="SCANNING", started=at(1))
        store = SqlScanSweepStore(app_engine)
        store.mark_requeued(queued, at(20))
        store.mark_requeued(running, at(20))
        with app_engine.connect() as conn:
            marks = dict(conn.execute(select(t.scans.c.id, t.scans.c.requeued_at)).all())
        assert marks[queued.value] == at(20)
        assert marks[running.value] is None
        assert store.queued_before(at(60), 10)[0].requeued_at == at(20)


class TestRetentionSimpleTables:
    def test_webhook_deliveries_older_than_the_cutoff_are_deleted_in_batches(
        self, app_engine: Engine, maintenance_engine: Engine
    ) -> None:
        with app_engine.begin() as conn:
            for n in range(5):
                conn.execute(
                    insert(t.webhook_deliveries).values(
                        delivery_id=f"00000000-0000-4000-8000-00000000000{n}",
                        event="push",
                        received_at=at(n * 10),
                        outcome="ACCEPTED",
                    )
                )
        store = SqlRetentionStore(maintenance_engine)
        assert store.purge_webhook_deliveries(at(35), 2) == 2
        assert store.purge_webhook_deliveries(at(35), 2) == 2
        assert store.purge_webhook_deliveries(at(35), 2) == 0
        with app_engine.connect() as conn:
            left = conn.execute(select(func.count()).select_from(t.webhook_deliveries)).scalar()
        assert left == 1

    def test_scan_logs_older_than_the_cutoff_are_deleted(
        self, app_engine: Engine, maintenance_engine: Engine
    ) -> None:
        sid = put_scan(app_engine, 1, status="QUEUED")
        with app_engine.begin() as conn:
            for seq in range(4):
                conn.execute(
                    insert(t.scan_logs).values(
                        scan_id=sid.value, seq=seq, at=at(seq * 10), level="INFO", message="m"
                    )
                )
        assert SqlRetentionStore(maintenance_engine).purge_scan_logs(at(25), 10) == 3
        with app_engine.connect() as conn:
            assert conn.execute(select(t.scan_logs.c.seq)).scalars().all() == [3]

    def test_only_published_outbox_events_are_ever_deleted(
        self, app_engine: Engine, maintenance_engine: Engine
    ) -> None:
        put_outbox(app_engine, 1, published=at(5))
        put_outbox(app_engine, 2, published=at(500))
        put_outbox(app_engine, 3, published=None)
        assert SqlRetentionStore(maintenance_engine).purge_published_outbox(at(100), 10) == 1
        with app_engine.connect() as conn:
            left = conn.execute(select(t.outbox_events.c.payload["n"].as_integer())).scalars().all()
        assert sorted(left) == [2, 3]


def put_finding(engine: Engine, n: int, first: ScanId, last: ScanId | None = None) -> None:
    finding = new_finding(n, scan=first)
    factory = SqlUnitOfWorkFactory(engine)
    with factory() as tx:
        tx.findings.add(finding)
        tx.commit()
    if last is not None:
        with engine.begin() as conn:
            conn.execute(
                update(t.findings)
                .where(t.findings.c.id == finding.id.value)
                .values(last_seen_scan_id=last.value)
            )


class TestRetentionOfScans:
    OLD = at(60)
    LONG_AGO = at(1000)

    def purge(self, maintenance_engine: Engine, limit: int = 100) -> int:
        return SqlRetentionStore(maintenance_engine).purge_scans(NOW - timedelta(days=365), limit)

    def test_old_unreferenced_scans_go_with_their_children(
        self, app_engine: Engine, maintenance_engine: Engine
    ) -> None:
        keeper = put_scan(app_engine, 1, status="COMPLETED", finished=at(30 * 24 * 60 * 399))
        old = put_scan(app_engine, 2, status="FAILED", finished=self.OLD)
        put_finding(app_engine, 1, first=keeper)
        with app_engine.begin() as conn:
            conn.execute(
                insert(t.scan_logs).values(
                    scan_id=old.value, seq=0, at=at(1), level="INFO", message="m"
                )
            )
            conn.execute(
                insert(t.policy_evaluations).values(scan_id=old.value, verdict="PASS", reasons=[])
            )
            conn.execute(
                insert(t.finding_occurrences).values(
                    scan_id=old.value,
                    finding_id=new_finding(1, scan=keeper).id.value,
                    message="m",
                    is_new=True,
                )
            )
        assert self.purge(maintenance_engine) == 1
        assert scan_ids(app_engine) == {1}
        with app_engine.connect() as conn:
            for table in (t.scan_logs, t.policy_evaluations, t.finding_occurrences):
                assert conn.execute(select(func.count()).select_from(table)).scalar() == 0

    def test_recent_and_unfinished_scans_are_kept(
        self, app_engine: Engine, maintenance_engine: Engine
    ) -> None:
        put_scan(app_engine, 1, status="FAILED", finished=NOW - timedelta(days=10))
        put_scan(app_engine, 2, status="QUEUED", queued=self.OLD)
        put_scan(app_engine, 3, status="SCANNING", queued=self.OLD, started=self.OLD)
        assert self.purge(maintenance_engine) == 0
        assert scan_ids(app_engine) == {1, 2, 3}

    @pytest.mark.parametrize("seen", ["first", "last"])
    def test_scans_a_finding_still_points_to_are_kept(
        self, app_engine: Engine, maintenance_engine: Engine, seen: str
    ) -> None:
        referenced = put_scan(app_engine, 1, status="FAILED", finished=self.OLD)
        other = put_scan(app_engine, 2, status="FAILED", finished=NOW)
        if seen == "first":
            put_finding(app_engine, 1, first=referenced, last=other)
        else:
            put_finding(app_engine, 1, first=other, last=referenced)
        assert self.purge(maintenance_engine) == 0
        assert scan_ids(app_engine) == {1, 2}

    def test_the_default_branch_baseline_survives_but_older_completed_scans_do_not(
        self, app_engine: Engine, maintenance_engine: Engine
    ) -> None:
        put_scan(app_engine, 1, status="COMPLETED", ref="main", finished=at(100))
        put_scan(app_engine, 2, status="COMPLETED", ref="refs/heads/main", finished=at(200))
        put_scan(app_engine, 3, status="COMPLETED", ref="feature/x", finished=at(300))
        put_scan(
            app_engine, 4, status="COMPLETED", ref="main", finished=at(150), repository=OTHER_REPO
        )
        assert self.purge(maintenance_engine) == 2
        # 2 es el baseline de REPO (la rama se escribe de dos formas); 4 es el de OTHER_REPO.
        assert scan_ids(app_engine) == {2, 4}

    def test_a_failed_newer_scan_does_not_displace_the_baseline(
        self, app_engine: Engine, maintenance_engine: Engine
    ) -> None:
        put_scan(app_engine, 1, status="COMPLETED", ref="main", finished=at(100))
        put_scan(app_engine, 2, status="FAILED", ref="main", finished=at(200))
        assert self.purge(maintenance_engine) == 1
        assert scan_ids(app_engine) == {1}

    def test_a_scan_that_supersedes_another_waits_until_the_older_one_is_gone(
        self, app_engine: Engine, maintenance_engine: Engine
    ) -> None:
        newer = put_scan(app_engine, 2, status="COMPLETED", ref="feature/x", finished=at(300))
        put_scan(
            app_engine,
            1,
            status="CANCELLED",
            ref="feature/x",
            finished=at(100),
            superseded_by=newer,
        )
        assert self.purge(maintenance_engine) == 1
        assert scan_ids(app_engine) == {2}
        assert self.purge(maintenance_engine) == 1
        assert scan_ids(app_engine) == set()

    def test_batches_are_bounded_and_oldest_first(
        self, app_engine: Engine, maintenance_engine: Engine
    ) -> None:
        for n in range(1, 6):
            put_scan(app_engine, n, status="FAILED", finished=at(n * 10))
        assert self.purge(maintenance_engine, limit=2) == 2
        assert scan_ids(app_engine) == {3, 4, 5}


class TestPrivileges:
    def test_maintenance_cannot_touch_audit_triage_or_findings(
        self, maintenance_engine: Engine
    ) -> None:
        for statement in (
            "DELETE FROM audit_log",
            "DELETE FROM triage_decisions",
            "DELETE FROM findings",
            "DELETE FROM repositories",
            "UPDATE scans SET version = 99",
        ):
            with (
                pytest.raises(DBAPIError, match="permission denied"),
                maintenance_engine.begin() as c,
            ):
                c.execute(text(statement))

    def test_the_application_role_cannot_run_retention_deletes(self, app_engine: Engine) -> None:
        for statement in (
            "DELETE FROM scans",
            "DELETE FROM webhook_deliveries",
            "DELETE FROM scan_logs",
        ):
            with pytest.raises(DBAPIError, match="permission denied"), app_engine.begin() as c:
                c.execute(text(statement))

    def test_the_application_role_can_relay_and_sweep(self, app_engine: Engine) -> None:
        put_outbox(app_engine, 1)
        put_scan(app_engine, 1, status="QUEUED", queued=at(1))
        assert SqlOutboxStore(app_engine, FakeClock(NOW)).relay_batch(5, lambda _: None) == 1
        SqlScanSweepStore(app_engine).mark_requeued(scan_id(1), at(5))


class TestPeriodicJobsEndToEnd:
    """Los casos de uso con los adaptadores SQL reales, sin fakes de persistencia."""

    def build(self, app_engine: Engine, now: datetime) -> tuple[Any, Any]:
        queue, clock = RecordingQueue(), FakeClock(now)
        abandon = AbandonScan(
            uow_factory=SqlUnitOfWorkFactory(app_engine), queue=queue, clock=clock
        )
        sweep = SweepStuckScans(
            store=SqlScanSweepStore(app_engine),
            queue=queue,
            abandon=abandon,
            clock=clock,
            thresholds=SweepThresholds(running_stuck_after=timedelta(minutes=50)),
        )
        return sweep, queue

    def test_sweep_requeues_then_expires_and_recovers_running_scans(
        self, app_engine: Engine
    ) -> None:
        lost = put_scan(app_engine, 1, status="QUEUED", queued=at(0))
        fresh = put_scan(app_engine, 2, status="QUEUED", queued=at(50))
        stuck = put_scan(app_engine, 3, status="SCANNING", queued=at(0), started=at(1))
        sweep, queue = self.build(app_engine, at(55))
        report = sweep.execute()
        assert (report.requeued, report.abandoned_running) == (1, 1)
        assert queue.enqueued.count((ScanStep.EXECUTE, lost)) == 1
        assert (ScanStep.EXECUTE, fresh) not in queue.enqueued
        assert (ScanStep.REPORT, stuck) in queue.enqueued
        assert sweep.execute().requeued == 0  # una sola vez
        sweep_later, queue_later = self.build(app_engine, at(125))
        assert sweep_later.execute().expired_in_queue == 2
        with app_engine.connect() as conn:
            rows = {
                r.id: (r.status, r.status_reason_code)
                for r in conn.execute(
                    select(t.scans.c.id, t.scans.c.status, t.scans.c.status_reason_code)
                )
            }
        assert rows[lost.value] == ("TIMED_OUT", "QUEUE_EXPIRED")
        assert rows[fresh.value] == ("TIMED_OUT", "QUEUE_EXPIRED")
        assert rows[stuck.value] == ("TIMED_OUT", "TIMEOUT")
        assert (ScanStep.REPORT, lost) in queue_later.enqueued

    def test_the_outbox_events_written_by_a_state_change_reach_the_relay(
        self, app_engine: Engine
    ) -> None:
        put_scan(app_engine, 1, status="QUEUED", queued=at(0))
        sweep, _ = self.build(app_engine, at(125))
        # expirar escribe un ScanStatusChanged en el outbox, en la misma transacción
        sweep.execute()
        seen: list[OutboxMessage] = []

        class Capture:
            def deliver(self, messages: Sequence[OutboxMessage]) -> None:
                seen.extend(messages)

        relay = RelayOutbox(store=SqlOutboxStore(app_engine, FakeClock(NOW)), sinks=[Capture()])
        assert relay.execute() == 1
        assert seen[0].type == "ScanStatusChanged"
        assert seen[0].payload["current"] == "TIMED_OUT"
        assert relay.execute() == 0
