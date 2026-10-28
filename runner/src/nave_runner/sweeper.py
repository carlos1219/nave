"""Barrido de recursos huérfanos (AM-505): contenedores y volúmenes cuya liberación
falló o cuyo worker murió. Se borra lo que supera la edad máxima; nunca lo reciente."""

import logging
import threading
from datetime import UTC, datetime, timedelta

from nave_runner.engine import Engine, EngineError

_LOG = logging.getLogger("nave_runner.sweeper")


def sweep_once(engine: Engine, *, max_age: timedelta, now: datetime) -> int:
    removed = 0
    for orphan in engine.list_orphans():
        if now - orphan.created_at < max_age:
            continue
        try:
            engine.remove_orphan(orphan)
        except EngineError:
            _LOG.warning("orphan removal failed", extra={"step": orphan.kind})
        else:
            removed += 1
    return removed


def run_forever(
    engine: Engine, *, max_age: timedelta, interval_seconds: int, stop: threading.Event
) -> None:
    while not stop.wait(interval_seconds):
        try:
            removed = sweep_once(engine, max_age=max_age, now=datetime.now(UTC))
        except EngineError:
            _LOG.warning("orphan sweep postponed")
            continue
        if removed:
            _LOG.info("orphans removed", extra={"count": removed})
