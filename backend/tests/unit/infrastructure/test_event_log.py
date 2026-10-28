import json
import logging
from datetime import UTC, datetime

import pytest

from nave.application.dto.maintenance import OutboxMessage
from nave.infrastructure.observability.event_log import LogEventSink
from nave.infrastructure.observability.logging import JsonFormatter

SCAN = "11111111-1111-4111-8111-111111111111"


def message(type_: str, payload: dict[str, object]) -> OutboxMessage:
    return OutboxMessage(id=1, type=type_, payload=payload, occurred_at=datetime.now(UTC))


def render(caplog: pytest.LogCaptureFixture) -> list[dict[str, object]]:
    formatter = JsonFormatter("nave-worker")
    return [json.loads(formatter.format(r)) for r in caplog.records if r.name == "nave.events"]


def test_well_formed_events_become_one_structured_line_each(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    LogEventSink().deliver(
        [
            message("ScanStatusChanged", {"scan_id": SCAN, "current": "COMPLETED"}),
            message("FindingTriaged", {"finding_id": SCAN}),
        ]
    )
    first, second = render(caplog)
    assert (first["event_type"], first["scan_id"], first["status"]) == (
        "ScanStatusChanged",
        SCAN,
        "COMPLETED",
    )
    assert "scan_id" not in second


@pytest.mark.parametrize(
    "hostile",
    [
        'x\n{"level":"CRITICAL"}',
        "A" * 10_000,
        "../../etc/passwd",
        "ghs_" + "a" * 36,
        {"nested": "value"},
        ["list"],
        None,
        7,
    ],
)
def test_untrusted_payload_values_never_reach_the_log(
    caplog: pytest.LogCaptureFixture, hostile: object
) -> None:
    caplog.set_level(logging.INFO)
    LogEventSink().deliver([message("ScanStatusChanged", {"scan_id": hostile, "current": hostile})])
    (entry,) = render(caplog)
    assert "scan_id" not in entry
    assert "status" not in entry
    assert "ghs_" not in json.dumps(entry)


def test_a_hostile_event_type_is_dropped_not_logged(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    LogEventSink().deliver([message("Bad\nType", {})])
    (entry,) = render(caplog)
    assert "event_type" not in entry
