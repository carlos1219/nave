"""Exportación CSV/SARIF (ADR 0029), cursor de la cola de triage y `tools` del resumen."""

from datetime import UTC, datetime
from uuid import UUID

import pytest

from nave.application.dto.insights import QueueCursor
from nave.application.services.queue_cursor import decode_queue_cursor, encode_queue_cursor
from nave.application.services.session_tokens import public_session_id
from nave.domain.errors import InvalidValueError
from nave.domain.quality_gates import Verdict
from nave.domain.value_objects.identifiers import FindingId
from nave.domain.value_objects.scan_summary import ScanSummary
from nave.domain.value_objects.scanner_kind import ScannerKind
from nave.domain.value_objects.severity_counts import SeverityCounts
from nave.domain.value_objects.tool_run import ToolRun
from nave.infrastructure.export.finding_export import safe_cell
from nave.infrastructure.persistence.codec import decode_summary, encode_summary

DIGEST = "sha256:" + "c" * 64


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("=1+1", "'=1+1"),
        ("+SUM(A1)", "'+SUM(A1)"),
        ("-2", "'-2"),
        ("@cmd", "'@cmd"),
        ("  =evil()", "'  =evil()"),
        ("\t=evil()", "'=evil()"),
        ("\r=evil()", "'=evil()"),
        ("lib/a.py", "lib/a.py"),
        ("line\nbreak", "linebreak"),
        ("", ""),
    ],
)
def test_csv_cells_cannot_become_formulas(raw: str, expected: str) -> None:
    assert safe_cell(raw) == expected


def test_queue_cursor_round_trip_and_rejects_anything_else() -> None:
    cursor = QueueCursor(
        rank=4,
        first_seen_at=datetime(2026, 10, 27, 9, 30, 1, 123456, tzinfo=UTC),
        finding_id=FindingId(UUID(int=0xABC)),
    )
    assert decode_queue_cursor(encode_queue_cursor(cursor)) == cursor
    for bad in ("", "4_1", "44_1_" + "0" * 32, "4_1_" + "G" * 32, "4_-1_" + "0" * 32):
        with pytest.raises(InvalidValueError):
            decode_queue_cursor(bad)


def test_public_session_id_is_not_the_stored_hash() -> None:
    stored = bytes(range(32))
    shown = public_session_id(stored)
    assert len(shown) == 32
    assert shown not in stored.hex()
    assert public_session_id(stored) == shown


def test_summary_tools_round_trip_and_old_summaries_still_decode() -> None:
    summary = ScanSummary(
        new=SeverityCounts(),
        open=SeverityCounts(high=1),
        verdict=Verdict.PASS,
        truncated=False,
        tools=(ToolRun(ScannerKind("trivy"), "0.58.0", DIGEST, 2),),
    )
    assert decode_summary(encode_summary(summary)) == summary
    legacy = encode_summary(ScanSummary(SeverityCounts(), SeverityCounts(), Verdict.PASS, False))
    assert "tools" not in legacy
    assert decode_summary(legacy).tools == ()


@pytest.mark.parametrize(
    "tools",
    [
        "x",
        [{"scanner": "trivy", "version": "1", "image_digest": DIGEST}],
        [{"scanner": "trivy", "version": "1 2", "image_digest": DIGEST, "findings": 1}],
        [{"scanner": "trivy", "version": "1", "image_digest": "sha256:x", "findings": 1}],
        [{"scanner": "trivy", "version": "1", "image_digest": DIGEST, "findings": -1}],
        [{"scanner": "trivy", "version": "1", "image_digest": DIGEST, "findings": 1, "x": 1}],
    ],
)
def test_corrupt_tools_are_rejected(tools: object) -> None:
    raw = encode_summary(ScanSummary(SeverityCounts(), SeverityCounts(), Verdict.PASS, False))
    raw["tools"] = tools  # type: ignore[assignment]
    with pytest.raises(InvalidValueError):
        decode_summary(raw)
