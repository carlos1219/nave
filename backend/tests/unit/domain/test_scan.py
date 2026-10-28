from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone

import pytest

from nave.domain.entities.scan import MAX_PULL_REQUEST_NUMBER, Scan
from nave.domain.errors import (
    InvalidScanTransitionError,
    InvalidTimestampError,
    ScanInvariantError,
)
from nave.domain.events import ScanQueued
from nave.domain.value_objects.scan_status import ScanStatus
from nave.domain.value_objects.status_reason import ReasonCode, StatusReason
from nave.domain.value_objects.trigger import ScanTrigger
from tests.unit.domain.builders import (
    OTHER_SCAN_ID,
    SCAN_ID,
    SUMMARY,
    T0,
    USER_ID,
    advance,
    at,
    new_queued_scan,
)

SCANNER_FAILED = StatusReason(ReasonCode.SCANNER_FAILED, "exit code 2")
TIMEOUT = StatusReason(ReasonCode.TIMEOUT)


class TestQueue:
    def test_creates_queued_scan_and_event(self) -> None:
        scan, event = Scan.queue(
            scan_id=SCAN_ID,
            repository_id=new_queued_scan().repository_id,
            commit=new_queued_scan().commit,
            ref=new_queued_scan().ref,
            trigger=ScanTrigger.MANUAL,
            trigger_key=new_queued_scan().trigger_key,
            at=T0,
            requested_by=USER_ID,
            pull_request_number=7,
        )
        assert scan.status is ScanStatus.QUEUED
        assert scan.queued_at == T0
        assert scan.started_at is None
        assert scan.finished_at is None
        assert not scan.is_terminal
        assert isinstance(event, ScanQueued)
        assert (event.scan_id, event.occurred_at, event.trigger) == (
            SCAN_ID,
            T0,
            ScanTrigger.MANUAL,
        )

    def test_manual_scan_requires_requester(self) -> None:
        with pytest.raises(ScanInvariantError):
            new_queued_scan(trigger=ScanTrigger.MANUAL)

    def test_webhook_scan_cannot_have_requester(self) -> None:
        with pytest.raises(ScanInvariantError):
            new_queued_scan(requested_by=USER_ID)

    @pytest.mark.parametrize("number", [0, -1, MAX_PULL_REQUEST_NUMBER + 1, True])
    def test_rejects_out_of_range_pull_request_numbers(self, number: int) -> None:
        with pytest.raises(ScanInvariantError):
            new_queued_scan(pull_request_number=number)

    def test_accepts_boundary_pull_request_number(self) -> None:
        scan = new_queued_scan(pull_request_number=MAX_PULL_REQUEST_NUMBER)
        assert scan.pull_request_number == MAX_PULL_REQUEST_NUMBER


class TestHappyPath:
    def test_full_lifecycle_emits_one_event_per_transition(self, queued_scan: Scan) -> None:
        fetching, e1 = queued_scan.start_fetching(at(1))
        scanning, e2 = fetching.start_scanning(at(2))
        processing, e3 = scanning.start_processing(at(3))
        completed, e4 = processing.complete(at(4), SUMMARY)

        assert [e.current for e in (e1, e2, e3, e4)] == [
            ScanStatus.FETCHING,
            ScanStatus.SCANNING,
            ScanStatus.PROCESSING,
            ScanStatus.COMPLETED,
        ]
        assert [e.previous for e in (e1, e2, e3, e4)] == [
            ScanStatus.QUEUED,
            ScanStatus.FETCHING,
            ScanStatus.SCANNING,
            ScanStatus.PROCESSING,
        ]
        assert all(e.reason is None for e in (e1, e2, e3, e4))
        assert completed.started_at == at(1)
        assert completed.finished_at == at(4)
        assert completed.is_terminal
        assert completed.status_reason is None
        assert completed.summary == SUMMARY

    def test_transitions_do_not_mutate_the_original(self, queued_scan: Scan) -> None:
        fetching, _ = queued_scan.start_fetching(at(1))
        assert queued_scan.status is ScanStatus.QUEUED
        assert queued_scan.started_at is None
        assert fetching is not queued_scan

    def test_entity_is_immutable(self, queued_scan: Scan) -> None:
        with pytest.raises(FrozenInstanceError):
            queued_scan.status = ScanStatus.COMPLETED  # type: ignore[misc]

    def test_version_is_preserved(self, queued_scan: Scan) -> None:
        loaded = replace(queued_scan, version=5)
        fetching, _ = loaded.start_fetching(at(1))
        assert fetching.version == 5


class TestUnsuccessfulEndings:
    @pytest.mark.parametrize("state", ["FETCHING", "SCANNING", "PROCESSING"])
    def test_fail_from_running_states_records_reason(self, queued_scan: Scan, state: str) -> None:
        running = advance(queued_scan, state)
        failed, event = running.fail(at(10), SCANNER_FAILED)
        assert failed.status is ScanStatus.FAILED
        assert failed.status_reason == SCANNER_FAILED
        assert failed.finished_at == at(10)
        assert event.reason == SCANNER_FAILED

    def test_cannot_fail_a_queued_scan(self, queued_scan: Scan) -> None:
        with pytest.raises(InvalidScanTransitionError):
            queued_scan.fail(at(1), SCANNER_FAILED)

    @pytest.mark.parametrize(
        "code", [ReasonCode.USER_CANCELLED, ReasonCode.SUPERSEDED, ReasonCode.TIMEOUT]
    )
    def test_fail_rejects_reasons_of_other_states(
        self, queued_scan: Scan, code: ReasonCode
    ) -> None:
        running = advance(queued_scan, "SCANNING")
        with pytest.raises(ScanInvariantError):
            running.fail(at(10), StatusReason(code))

    def test_queued_scan_can_expire(self, queued_scan: Scan) -> None:
        expired, _ = queued_scan.time_out(at(60), StatusReason(ReasonCode.QUEUE_EXPIRED))
        assert expired.status is ScanStatus.TIMED_OUT
        assert expired.started_at is None
        assert expired.finished_at == at(60)

    def test_time_out_rejects_failure_reasons(self, queued_scan: Scan) -> None:
        with pytest.raises(ScanInvariantError):
            queued_scan.time_out(at(60), SCANNER_FAILED)

    @pytest.mark.parametrize("state", ["QUEUED", "FETCHING", "SCANNING"])
    def test_cancel_from_cancellable_states(self, queued_scan: Scan, state: str) -> None:
        scan = queued_scan if state == "QUEUED" else advance(queued_scan, state)
        cancelled, event = scan.cancel(at(10))
        assert cancelled.status is ScanStatus.CANCELLED
        assert cancelled.status_reason == StatusReason(ReasonCode.USER_CANCELLED)
        assert cancelled.superseded_by is None
        assert event.current is ScanStatus.CANCELLED

    def test_processing_cannot_be_cancelled(self, queued_scan: Scan) -> None:
        processing = advance(queued_scan, "PROCESSING")
        with pytest.raises(InvalidScanTransitionError):
            processing.cancel(at(10))

    def test_supersede_links_the_newer_scan(self, queued_scan: Scan) -> None:
        superseded, _ = queued_scan.supersede(OTHER_SCAN_ID, at(1))
        assert superseded.status is ScanStatus.CANCELLED
        assert superseded.superseded_by == OTHER_SCAN_ID
        assert superseded.status_reason == StatusReason(ReasonCode.SUPERSEDED)

    def test_cannot_supersede_itself(self, queued_scan: Scan) -> None:
        with pytest.raises(ScanInvariantError):
            queued_scan.supersede(SCAN_ID, at(1))

    def test_terminal_scans_accept_no_further_transitions(self, queued_scan: Scan) -> None:
        completed = advance(queued_scan, "COMPLETED")
        for transition in (
            completed.start_fetching,
            completed.start_scanning,
            completed.start_processing,
            lambda moment: completed.complete(moment, SUMMARY),
            completed.cancel,
        ):
            with pytest.raises(InvalidScanTransitionError):
                transition(at(20))
        with pytest.raises(InvalidScanTransitionError):
            completed.fail(at(20), SCANNER_FAILED)


class TestTimestamps:
    def test_rejects_naive_datetime(self, queued_scan: Scan) -> None:
        with pytest.raises(InvalidTimestampError):
            queued_scan.start_fetching(datetime(2026, 10, 11, 9, 0))  # noqa: DTZ001

    def test_rejects_non_utc_offset(self, queued_scan: Scan) -> None:
        madrid = timezone(timedelta(hours=2))
        with pytest.raises(InvalidTimestampError):
            queued_scan.start_fetching(datetime(2026, 10, 11, 12, 0, tzinfo=madrid))

    def test_rejects_non_datetime(self, queued_scan: Scan) -> None:
        with pytest.raises(InvalidTimestampError):
            queued_scan.start_fetching("2026-10-11T09:00:00Z")  # type: ignore[arg-type]

    def test_rejects_time_going_backwards(self, queued_scan: Scan) -> None:
        fetching = advance(queued_scan, "FETCHING")
        with pytest.raises(InvalidTimestampError):
            fetching.start_scanning(T0)

    def test_accepts_equal_timestamps(self, queued_scan: Scan) -> None:
        fetching, _ = queued_scan.start_fetching(T0)
        assert fetching.started_at == T0


class TestRehydrationInvariants:
    """Una fila incoherente de la base de datos debe fallar al cargarse."""

    def test_queued_with_started_at(self, queued_scan: Scan) -> None:
        with pytest.raises(ScanInvariantError):
            replace(queued_scan, started_at=at(1))

    def test_running_without_started_at(self, queued_scan: Scan) -> None:
        with pytest.raises(ScanInvariantError):
            replace(queued_scan, status=ScanStatus.SCANNING)

    def test_completed_without_started_at(self, queued_scan: Scan) -> None:
        with pytest.raises(ScanInvariantError):
            replace(queued_scan, status=ScanStatus.COMPLETED, finished_at=at(1))

    def test_terminal_without_finished_at(self, queued_scan: Scan) -> None:
        completed = advance(queued_scan, "COMPLETED")
        with pytest.raises(ScanInvariantError):
            replace(completed, finished_at=None)

    def test_running_with_finished_at(self, queued_scan: Scan) -> None:
        scanning = advance(queued_scan, "SCANNING")
        with pytest.raises(ScanInvariantError):
            replace(scanning, finished_at=at(5))

    def test_failed_without_reason(self, queued_scan: Scan) -> None:
        failed, _ = advance(queued_scan, "SCANNING").fail(at(5), SCANNER_FAILED)
        with pytest.raises(ScanInvariantError):
            replace(failed, status_reason=None)

    def test_completed_with_reason(self, queued_scan: Scan) -> None:
        completed = advance(queued_scan, "COMPLETED")
        with pytest.raises(ScanInvariantError):
            replace(completed, status_reason=SCANNER_FAILED)

    def test_superseded_by_without_superseded_reason(self, queued_scan: Scan) -> None:
        cancelled, _ = queued_scan.cancel(at(1))
        with pytest.raises(ScanInvariantError):
            replace(cancelled, superseded_by=OTHER_SCAN_ID)

    def test_superseded_reason_without_link(self, queued_scan: Scan) -> None:
        superseded, _ = queued_scan.supersede(OTHER_SCAN_ID, at(1))
        with pytest.raises(ScanInvariantError):
            replace(superseded, superseded_by=None)

    def test_started_before_queued(self, queued_scan: Scan) -> None:
        fetching = advance(queued_scan, "FETCHING")
        with pytest.raises(ScanInvariantError):
            replace(fetching, started_at=T0 - timedelta(seconds=1))

    def test_finished_before_started(self, queued_scan: Scan) -> None:
        completed = advance(queued_scan, "COMPLETED")
        with pytest.raises(ScanInvariantError):
            replace(completed, finished_at=at(0))

    def test_finished_before_queued_when_never_started(self, queued_scan: Scan) -> None:
        cancelled, _ = queued_scan.cancel(at(1))
        with pytest.raises(ScanInvariantError):
            replace(cancelled, finished_at=T0 - timedelta(seconds=1))

    def test_non_utc_stored_timestamp(self, queued_scan: Scan) -> None:
        with pytest.raises(InvalidTimestampError):
            replace(queued_scan, queued_at=datetime(2026, 10, 11, 8, 0))  # noqa: DTZ001

    @pytest.mark.parametrize("version", [-1, True, 1.5, 2**63])
    def test_invalid_version(self, queued_scan: Scan, version: int) -> None:
        with pytest.raises(ScanInvariantError):
            replace(queued_scan, version=version)


class TestSummary:
    def test_completed_without_summary_is_invalid(self, queued_scan: Scan) -> None:
        completed = advance(queued_scan, "COMPLETED")
        with pytest.raises(ScanInvariantError):
            replace(completed, summary=None)

    def test_non_completed_with_summary_is_invalid(self, queued_scan: Scan) -> None:
        with pytest.raises(ScanInvariantError):
            replace(queued_scan, summary=SUMMARY)
