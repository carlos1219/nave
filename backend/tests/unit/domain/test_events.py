from datetime import datetime

import pytest

from nave.domain.errors import InvalidTimestampError
from nave.domain.events import ScanStatusChanged
from nave.domain.value_objects.scan_status import ScanStatus
from tests.unit.domain.builders import SCAN_ID


def test_event_requires_utc_timestamp() -> None:
    with pytest.raises(InvalidTimestampError):
        ScanStatusChanged(
            occurred_at=datetime(2026, 10, 11, 8, 0),  # noqa: DTZ001
            scan_id=SCAN_ID,
            previous=ScanStatus.QUEUED,
            current=ScanStatus.FETCHING,
            reason=None,
        )
