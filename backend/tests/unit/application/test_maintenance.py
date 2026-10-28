from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta

import pytest

from nave.application.dto.maintenance import (
    OutboxMessage,
    RetentionPolicy,
    RetentionReport,
    StaleScan,
    SweepThresholds,
)
from nave.application.errors import ConcurrentModificationError, QueueUnavailableError
from nave.application.ports.messaging import ScanStep
from nave.application.use_cases.purge_expired_data import PurgeExpiredData
from nave.application.use_cases.relay_outbox import RelayOutbox
from nave.application.use_cases.sweep_stuck_scans import SweepStuckScans
from nave.domain.errors import InvalidValueError
from nave.domain.value_objects.identifiers import ScanId
from nave.domain.value_objects.scan_status import ScanStatus
from nave.domain.value_objects.status_reason import ReasonCode
from tests.unit.application.harness import Harness, make_harness
from tests.unit.application.test_use_cases import scan_of, start

NOW = datetime(2026, 10, 12, 9, 0).astimezone()


def message(n: int) -> OutboxMessage:
    return OutboxMessage(id=n, type="ScanQueued", payload={}, occurred_at=NOW)


@dataclass
class FakeOutbox:
    pending: list[OutboxMessage] = field(default_factory=list)
    published: list[int] = field(default_factory=list)

    def relay_batch(self, limit: int, deliver: Callable[[Sequence[OutboxMessage]], None]) -> int:
        batch = self.pending[:limit]
        if not batch:
            return 0
        deliver(batch)  # si lanza, no se marca nada: igual que la transacción real
        self.published += [m.id for m in batch]
        self.pending = self.pending[limit:]
        return len(batch)


class Sink:
    def __init__(self, error: Exception | None = None) -> None:
        self.error, self.received = error, []

    def deliver(self, messages: Sequence[OutboxMessage]) -> None:
        if self.error:
            raise self.error
        self.received.append([m.id for m in messages])


class TestRelayOutbox:
    def test_publishes_in_batches_until_the_outbox_is_empty(self) -> None:
        store, sink = FakeOutbox([message(n) for n in range(1, 6)]), Sink()
        total = RelayOutbox(store=store, sinks=[sink], batch_size=2).execute()
        assert total == 5
        assert sink.received == [[1, 2], [3, 4], [5]]

    def test_every_sink_sees_every_batch(self) -> None:
        store, a, b = FakeOutbox([message(1)]), Sink(), Sink()
        RelayOutbox(store=store, sinks=[a, b]).execute()
        assert a.received == b.received == [[1]]

    def test_a_failing_sink_leaves_the_batch_unpublished_for_the_next_run(self) -> None:
        store = FakeOutbox([message(1), message(2)])
        with pytest.raises(RuntimeError):
            RelayOutbox(store=store, sinks=[Sink(RuntimeError("down"))]).execute()
        assert store.published == []
        assert RelayOutbox(store=store, sinks=[Sink()]).execute() == 2

    def test_one_run_is_bounded_so_a_flood_cannot_monopolise_the_worker(self) -> None:
        store = FakeOutbox([message(n) for n in range(100)])
        assert (
            RelayOutbox(store=store, sinks=[Sink()], batch_size=10, max_batches=3).execute() == 30
        )
        assert len(store.pending) == 70

    def test_an_empty_outbox_is_a_no_op(self) -> None:
        assert RelayOutbox(store=FakeOutbox(), sinks=[Sink()]).execute() == 0


class FakeSweepStore:
    def __init__(self, h: Harness) -> None:
        self._h = h
        self.requeued: dict[ScanId, datetime] = {}

    def _stale(self, statuses: set[ScanStatus], cutoff: datetime, limit: int) -> list[StaleScan]:
        rows = []
        for scan in self._h.store.state.scans.values():
            if scan.status in statuses:
                since = scan.queued_at if scan.status is ScanStatus.QUEUED else scan.started_at
                assert since is not None
                if since < cutoff:
                    rows.append(StaleScan(scan.id, scan.status, since, self.requeued.get(scan.id)))
        return sorted(rows, key=lambda r: r.since)[:limit]

    def queued_before(self, cutoff: datetime, limit: int) -> Sequence[StaleScan]:
        return self._stale({ScanStatus.QUEUED}, cutoff, limit)

    def running_before(self, cutoff: datetime, limit: int) -> Sequence[StaleScan]:
        running = {ScanStatus.FETCHING, ScanStatus.SCANNING, ScanStatus.PROCESSING}
        return self._stale(running, cutoff, limit)

    def mark_requeued(self, scan_id: ScanId, at: datetime) -> None:
        self.requeued[scan_id] = at


THRESHOLDS = SweepThresholds(
    running_stuck_after=timedelta(minutes=50),
    requeue_after=timedelta(minutes=10),
    expire_queued_after=timedelta(minutes=60),
)


def sweeper(
    h: Harness, store: FakeSweepStore | None = None
) -> tuple[SweepStuckScans, FakeSweepStore]:
    store = store or FakeSweepStore(h)
    return (
        SweepStuckScans(
            store=store, queue=h.queue, abandon=h.abandon, clock=h.clock, thresholds=THRESHOLDS
        ),
        store,
    )


def executes(h: Harness, scan_id: ScanId) -> int:
    return h.queue.enqueued.count((ScanStep.EXECUTE, scan_id))


def advance_scan_to(h: Harness, scan_id: ScanId, status: ScanStatus) -> None:
    scan = scan_of(h, scan_id)
    now = h.clock.now()
    scan, _ = scan.start_fetching(now)
    if status in {ScanStatus.SCANNING, ScanStatus.PROCESSING}:
        scan, _ = scan.start_scanning(now)
    if status is ScanStatus.PROCESSING:
        scan, _ = scan.start_processing(now)
    h.store.state.scans[scan_id] = scan


class TestSweepQueuedScans:
    def test_a_recent_scan_is_left_alone(self) -> None:
        h = make_harness()
        scan_id = start(h)
        h.clock.advance(minutes=9)
        report = sweeper(h)[0].execute()
        assert (report.requeued, report.expired_in_queue) == (0, 0)
        assert executes(h, scan_id) == 1

    def test_a_lost_scan_is_requeued_exactly_once(self) -> None:
        h = make_harness()
        scan_id = start(h)
        h.clock.advance(minutes=11)
        sweep, store = sweeper(h)
        assert sweep.execute().requeued == 1
        assert executes(h, scan_id) == 2
        assert store.requeued[scan_id] == h.clock.now()
        h.clock.advance(minutes=5)
        assert sweep.execute().requeued == 0
        assert executes(h, scan_id) == 2

    def test_a_scan_still_queued_after_the_expiry_window_expires_and_is_reported(self) -> None:
        h = make_harness()
        scan_id = start(h)
        h.clock.advance(minutes=61)
        report = sweeper(h)[0].execute()
        scan = scan_of(h, scan_id)
        assert report.expired_in_queue == 1
        assert scan.status is ScanStatus.TIMED_OUT
        assert scan.status_reason is not None
        assert scan.status_reason.code is ReasonCode.QUEUE_EXPIRED
        assert (ScanStep.REPORT, scan_id) in h.queue.enqueued
        assert executes(h, scan_id) == 1  # no se reencola lo que ya expira

    def test_a_full_queue_postpones_the_requeue_without_marking_it(self) -> None:
        h = make_harness()
        scan_id = start(h)
        h.clock.advance(minutes=11)

        def full(step: ScanStep, scan: ScanId) -> None:
            raise QueueUnavailableError

        original = h.queue.enqueue
        h.queue.enqueue = full  # type: ignore[method-assign]
        sweep, store = sweeper(h)
        report = sweep.execute()
        assert (report.requeued, report.skipped) == (0, 1)
        assert scan_id not in store.requeued
        h.queue.enqueue = original  # type: ignore[method-assign]
        assert sweep.execute().requeued == 1


class TestSweepRunningScans:
    @pytest.mark.parametrize("status", [ScanStatus.FETCHING, ScanStatus.SCANNING])
    def test_a_scan_running_too_long_times_out(self, status: ScanStatus) -> None:
        h = make_harness()
        scan_id = start(h)
        advance_scan_to(h, scan_id, status)
        h.clock.advance(minutes=51)
        report = sweeper(h)[0].execute()
        scan = scan_of(h, scan_id)
        assert report.abandoned_running == 1
        assert scan.status is ScanStatus.TIMED_OUT
        assert scan.status_reason is not None
        assert scan.status_reason.code is ReasonCode.TIMEOUT
        assert (ScanStep.REPORT, scan_id) in h.queue.enqueued

    def test_a_scan_stuck_in_processing_fails_with_an_internal_error(self) -> None:
        h = make_harness()
        scan_id = start(h)
        advance_scan_to(h, scan_id, ScanStatus.PROCESSING)
        h.clock.advance(minutes=51)
        sweeper(h)[0].execute()
        scan = scan_of(h, scan_id)
        assert scan.status is ScanStatus.FAILED
        assert scan.status_reason is not None
        assert scan.status_reason.code is ReasonCode.INTERNAL_ERROR

    def test_a_scan_within_its_budget_is_untouched(self) -> None:
        h = make_harness()
        scan_id = start(h)
        advance_scan_to(h, scan_id, ScanStatus.SCANNING)
        h.clock.advance(minutes=49)
        assert sweeper(h)[0].execute().abandoned_running == 0
        assert scan_of(h, scan_id).status is ScanStatus.SCANNING

    def test_a_concurrent_change_is_skipped_not_fatal(self) -> None:
        h = make_harness()
        scan_id = start(h)
        advance_scan_to(h, scan_id, ScanStatus.SCANNING)
        h.clock.advance(minutes=51)

        class Racing:
            def execute(self, command: object) -> bool:
                raise ConcurrentModificationError

        sweep = SweepStuckScans(
            store=FakeSweepStore(h),
            queue=h.queue,
            abandon=Racing(),  # type: ignore[arg-type]
            clock=h.clock,
            thresholds=THRESHOLDS,
        )
        report = sweep.execute()
        assert (report.abandoned_running, report.skipped) == (0, 1)

    def test_terminal_scans_are_never_touched(self) -> None:
        h = make_harness()
        scan_id = start(h)
        h.store.state.scans[scan_id], _ = scan_of(h, scan_id).cancel(h.clock.now())
        h.clock.advance(minutes=500)
        report = sweeper(h)[0].execute()
        assert (report.requeued, report.expired_in_queue, report.abandoned_running) == (0, 0, 0)


class FakeRetention:
    def __init__(self, counts: dict[str, int]) -> None:
        self.counts = counts
        self.calls: list[tuple[str, datetime, int]] = []

    def _purge(self, name: str, before: datetime, limit: int) -> int:
        self.calls.append((name, before, limit))
        take = min(self.counts.get(name, 0), limit)
        self.counts[name] = self.counts.get(name, 0) - take
        return take

    def purge_webhook_deliveries(self, before: datetime, limit: int) -> int:
        return self._purge("webhook", before, limit)

    def purge_webhook_counters(self, before: datetime, limit: int) -> int:
        return self._purge("counters", before, limit)

    def purge_scan_logs(self, before: datetime, limit: int) -> int:
        return self._purge("logs", before, limit)

    def purge_published_outbox(self, before: datetime, limit: int) -> int:
        return self._purge("outbox", before, limit)

    def purge_scans(self, before: datetime, limit: int) -> int:
        return self._purge("scans", before, limit)


class TestPurgeExpiredData:
    def test_cutoffs_follow_the_policy_and_batches_drain_each_table(self) -> None:
        h = make_harness()
        store = FakeRetention({"webhook": 25, "logs": 3, "outbox": 0, "scans": 10})
        policy = RetentionPolicy(batch_size=10, max_batches=10)
        report = PurgeExpiredData(store=store, clock=h.clock, policy=policy).execute()
        assert report == RetentionReport(25, 3, 0, 10)
        now = h.clock.now()
        cutoffs = {name: before for name, before, _ in store.calls}
        assert cutoffs["webhook"] == now - timedelta(days=30)
        assert cutoffs["logs"] == now - timedelta(days=30)
        assert cutoffs["outbox"] == now - timedelta(days=7)
        assert cutoffs["scans"] == now - timedelta(days=365)

    def test_a_run_never_exceeds_its_batch_budget(self) -> None:
        h = make_harness()
        store = FakeRetention({"webhook": 1000, "logs": 0, "outbox": 0, "scans": 0})
        policy = RetentionPolicy(batch_size=10, max_batches=3)
        assert (
            PurgeExpiredData(store=store, clock=h.clock, policy=policy).execute().webhook_deliveries
            == 30
        )
        assert store.counts["webhook"] == 970


class TestValidation:
    @pytest.mark.parametrize(
        "kwargs",
        [
            {"webhook_deliveries": timedelta(hours=23)},
            {"scan_logs": timedelta(0)},
            {"published_outbox": timedelta(hours=1)},
            {"scans": timedelta(days=29)},
            {"batch_size": 0},
            {"batch_size": 10_001},
            {"max_batches": 0},
        ],
    )
    def test_retention_floors_protect_dependent_features(self, kwargs: dict[str, object]) -> None:
        with pytest.raises(InvalidValueError):
            RetentionPolicy(**kwargs)  # type: ignore[arg-type]

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"running_stuck_after": timedelta(minutes=4)},
            {"requeue_after": timedelta(seconds=30)},
            {"requeue_after": timedelta(minutes=60), "expire_queued_after": timedelta(minutes=60)},
            {"batch_size": 0},
        ],
    )
    def test_sweep_thresholds_must_be_coherent(self, kwargs: dict[str, object]) -> None:
        base: dict[str, object] = {"running_stuck_after": timedelta(minutes=50)}
        with pytest.raises(InvalidValueError):
            SweepThresholds(**{**base, **kwargs})  # type: ignore[arg-type]

    def test_replace_keeps_validation(self) -> None:
        with pytest.raises(InvalidValueError):
            replace(RetentionPolicy(), scans=timedelta(days=1))
