from datetime import datetime

from nave.application.use_cases.scrub_orphaned_users import (
    BATCH_SIZE,
    MAX_BATCHES,
    SCRUB_GRACE,
    ScrubOrphanedUsers,
)
from tests.fakes.memory import FakeClock


class FakeStore:
    def __init__(self, *batches: int) -> None:
        self.batches = list(batches)
        self.asked: list[tuple[datetime, int]] = []

    def scrub_batch(self, last_login_before: datetime, limit: int) -> int:
        self.asked.append((last_login_before, limit))
        return self.batches.pop(0) if self.batches else 0


def test_asks_only_for_users_idle_for_longer_than_the_grace_period() -> None:
    store, clock = FakeStore(0), FakeClock()
    ScrubOrphanedUsers(store=store, clock=clock).execute()
    assert store.asked == [(clock.now() - SCRUB_GRACE, BATCH_SIZE)]


def test_stops_after_a_partial_batch_and_sums_the_total() -> None:
    store = FakeStore(BATCH_SIZE, BATCH_SIZE, 7, 999)
    assert ScrubOrphanedUsers(store=store, clock=FakeClock()).execute() == 2 * BATCH_SIZE + 7
    assert len(store.asked) == 3


def test_a_runaway_store_cannot_keep_the_task_busy_forever() -> None:
    store = FakeStore(*([BATCH_SIZE] * (MAX_BATCHES + 10)))
    ScrubOrphanedUsers(store=store, clock=FakeClock()).execute()
    assert len(store.asked) == MAX_BATCHES


def test_nothing_to_do_is_quiet(caplog: object) -> None:
    store = FakeStore(0)
    assert ScrubOrphanedUsers(store=store, clock=FakeClock()).execute() == 0
