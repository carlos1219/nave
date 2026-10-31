import logging
from datetime import UTC, datetime, timedelta

import pytest

from nave.domain.quality_gates import QualityGatePolicy, Verdict
from nave.infrastructure.observability.logging import JsonFormatter, configure_logging
from nave.infrastructure.policy.evaluator import QualityGateEvaluator
from nave.infrastructure.system import RandomIdGenerator, SystemClock


def test_the_system_clock_is_timezone_aware_utc() -> None:
    now = SystemClock().now()
    assert now.utcoffset() == timedelta(0)
    assert abs(now - datetime.now(UTC)) < timedelta(seconds=5)


def test_generated_ids_are_random_v4_uuids() -> None:
    ids = RandomIdGenerator()
    values = {ids.new_uuid() for _ in range(50)}
    assert len(values) == 50
    assert all(v.version == 4 for v in values)


def test_the_quality_gate_evaluator_delegates_to_the_domain() -> None:
    empty = QualityGatePolicy(version=1, gates=(), sla_days={}, fail_on_sla_breach=False)
    verdict = QualityGateEvaluator().evaluate(empty, (), datetime.now(UTC))
    assert verdict.verdict is Verdict.NEUTRAL


@pytest.mark.usefixtures("restore_root_logger")
def test_configure_logging_installs_a_single_json_handler() -> None:
    configure_logging("nave-worker", "WARNING")
    configure_logging("nave-worker", "WARNING")
    root = logging.getLogger()
    assert len(root.handlers) == 1
    assert isinstance(root.handlers[0].formatter, JsonFormatter)
    assert root.level == logging.WARNING
