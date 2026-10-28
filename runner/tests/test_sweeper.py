import threading
from datetime import timedelta

from nave_runner.engine import Orphan
from nave_runner.sweeper import run_forever, sweep_once
from tests.conftest import HOUR, NOW, FakeEngine


def test_only_orphans_older_than_the_limit_are_removed(engine: FakeEngine) -> None:
    old = Orphan("volume", "nave-ws-" + "a" * 32, NOW - 4 * HOUR)
    recent = Orphan("container", "nave-scan-" + "b" * 32, NOW - HOUR)
    edge = Orphan("volume", "nave-ws-" + "c" * 32, NOW - 3 * HOUR)
    engine.orphans = [old, recent, edge]
    assert sweep_once(engine, max_age=3 * HOUR, now=NOW) == 2
    assert engine.removed_orphans == [old, edge]


def test_a_failing_removal_does_not_stop_the_sweep(engine: FakeEngine) -> None:
    engine.orphans = [Orphan("volume", "nave-ws-" + "a" * 32, NOW - 9 * HOUR)]
    engine.fail_remove = True
    assert sweep_once(engine, max_age=HOUR, now=NOW) == 0


def test_nothing_to_do(engine: FakeEngine) -> None:
    assert sweep_once(engine, max_age=timedelta(0), now=NOW) == 0


def test_run_forever_stops_when_asked(engine: FakeEngine) -> None:
    stop = threading.Event()
    thread = threading.Thread(
        target=run_forever,
        kwargs={"engine": engine, "max_age": HOUR, "interval_seconds": 3600, "stop": stop},
    )
    thread.start()
    stop.set()
    thread.join(timeout=5)
    assert not thread.is_alive()
