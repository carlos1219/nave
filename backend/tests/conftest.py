import logging
from collections.abc import Iterator

import pytest


@pytest.fixture
def restore_root_logger() -> Iterator[None]:
    """Para tests que llaman a `configure_logging`, que sustituye los handlers globales."""
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    yield
    root.handlers[:], root.level = handlers, level
