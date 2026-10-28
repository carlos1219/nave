"""Punto de entrada: `python -m nave_runner`."""

import json
import logging
import os
import signal
import sys
import threading
from datetime import timedelta
from typing import Any

from nave_runner.config import ConfigurationError, load_config
from nave_runner.engine import CliEngine
from nave_runner.server import RunnerHTTPServer
from nave_runner.sweeper import run_forever

_FIELDS = ("run_id", "step", "outcome", "duration_s", "count")


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "level": record.levelname,
            "service": "nave-scan-runner",
            "event": record.getMessage()[:500],
        }
        entry.update({f: getattr(record, f) for f in _FIELDS if hasattr(record, f)})
        return json.dumps(entry, separators=(",", ":"))


def main() -> int:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(_JsonFormatter())
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
    log = logging.getLogger("nave_runner")
    try:
        config = load_config(os.environ)
    except ConfigurationError as error:
        log.error("invalid configuration: %s", error)  # noqa: TRY400 - sin traza: el mensaje nombra la variable
        return 2
    if config.runtime is None:
        log.warning(
            "no OCI runtime configured: containers share the host kernel (development only)"
        )
    engine = CliEngine(config.engine_binary)
    server = RunnerHTTPServer(config, engine)
    stop = threading.Event()
    threading.Thread(
        target=run_forever,
        kwargs={
            "engine": engine,
            "max_age": timedelta(seconds=config.orphan_max_age_seconds),
            "interval_seconds": config.sweep_interval_seconds,
            "stop": stop,
        },
        daemon=True,
    ).start()

    def terminate(_signum: int, _frame: object) -> None:
        stop.set()
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, terminate)
    signal.signal(signal.SIGINT, terminate)
    log.info("scan-runner listening")
    server.serve_forever()
    server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
