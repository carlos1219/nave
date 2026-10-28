import pytest
from hypothesis import settings

from nave.domain.entities.scan import Scan
from tests.unit.domain.builders import new_queued_scan

settings.register_profile("nave", max_examples=300, deadline=None)
settings.load_profile("nave")


@pytest.fixture
def queued_scan() -> Scan:
    return new_queued_scan()
