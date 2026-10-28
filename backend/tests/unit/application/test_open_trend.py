from datetime import UTC, date, datetime
from uuid import UUID

from nave.application.dto.insights import SummaryPoint
from nave.application.services.open_trend import build_trend, sum_counts
from nave.domain.value_objects.identifiers import RepositoryId
from nave.domain.value_objects.severity_counts import SeverityCounts

A = RepositoryId(UUID(int=1))
B = RepositoryId(UUID(int=2))


def point(repo: RepositoryId, day: int, hour: int, high: int) -> SummaryPoint:
    return SummaryPoint(repo, datetime(2026, 10, day, hour, tzinfo=UTC), SeverityCounts(high=high))


def test_each_day_sums_the_latest_count_of_every_repository() -> None:
    points = [point(A, 2, 9, 5), point(B, 3, 10, 2), point(A, 3, 23, 1), point(A, 5, 0, 4)]
    trend = build_trend(reversed(points), last_day=date(2026, 10, 5), days=5)
    assert [p.day.day for p in trend] == [1, 2, 3, 4, 5]
    assert [p.open.high for p in trend] == [0, 5, 3, 3, 6]


def test_points_before_the_window_are_the_starting_value() -> None:
    trend = build_trend([point(A, 1, 0, 7)], last_day=date(2026, 10, 10), days=3)
    assert [p.open.high for p in trend] == [7, 7, 7]


def test_sum_counts_adds_every_severity() -> None:
    total = sum_counts([SeverityCounts(critical=1, low=2), SeverityCounts(critical=3, info=1)])
    assert total == SeverityCounts(critical=4, low=2, info=1)
