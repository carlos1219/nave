"""ADR 0030: los rechazos de webhooks se acumulan y se vuelcan con intervalo mínimo."""

from collections.abc import Mapping
from datetime import datetime, timedelta

from nave.application.dto.platform import WebhookReason
from nave.application.errors import PersistenceUnavailableError
from nave.infrastructure.observability.webhook_stats import MAX_PENDING_KEYS, BufferedWebhookStats
from tests.fakes.memory import FakeClock, InMemoryWebhookCounters, Store


def stats(store: Store, clock: FakeClock) -> tuple[BufferedWebhookStats, InMemoryWebhookCounters]:
    counters = InMemoryWebhookCounters(store)
    return BufferedWebhookStats(store=counters, clock=clock), counters


def test_a_burst_costs_one_write_per_interval() -> None:
    store, clock = Store(), FakeClock()
    buffered, counters = stats(store, clock)
    for _ in range(10_000):
        buffered.count(WebhookReason.SIGNATURE)
    assert counters.flushes == 0
    clock.advance(seconds=31)
    buffered.count(WebhookReason.SIGNATURE)
    assert counters.flushes == 1
    assert sum(store.state.webhook_counters.values()) == 10_001


def test_counts_are_grouped_by_minute_and_reason() -> None:
    store, clock = Store(), FakeClock()
    buffered, _ = stats(store, clock)
    buffered.count(WebhookReason.DUPLICATE)
    clock.advance(seconds=61)
    buffered.count(WebhookReason.MALFORMED)
    reasons = {reason for _, reason in store.state.webhook_counters}
    assert reasons == {WebhookReason.DUPLICATE, WebhookReason.MALFORMED}


def test_memory_stays_bounded_when_the_store_is_never_reached() -> None:
    clock = FakeClock()
    buffered = BufferedWebhookStats(
        store=InMemoryWebhookCounters(Store()), clock=clock, flush_every=timedelta(days=365)
    )
    for _ in range(MAX_PENDING_KEYS * 2):
        clock.advance(minutes=1)
        buffered.count(WebhookReason.SIGNATURE)
    assert len(buffered._pending) <= MAX_PENDING_KEYS


class BrokenStore:
    def add(self, counts: Mapping[tuple[datetime, WebhookReason], int]) -> None:
        raise PersistenceUnavailableError("down")


def test_a_failing_store_never_breaks_the_request() -> None:
    clock = FakeClock()
    buffered = BufferedWebhookStats(store=BrokenStore(), clock=clock, flush_every=timedelta(0))
    buffered.count(WebhookReason.SIGNATURE)
