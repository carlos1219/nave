"""Serie diaria de findings abiertos de una organización a partir de los resúmenes de escaneo."""

from collections.abc import Iterable
from datetime import UTC, date, datetime, time, timedelta

from nave.application.dto.insights import SummaryPoint, TrendPoint
from nave.domain.value_objects.identifiers import RepositoryId
from nave.domain.value_objects.severity import Severity
from nave.domain.value_objects.severity_counts import SeverityCounts


def build_trend(
    points: Iterable[SummaryPoint], *, last_day: date, days: int
) -> tuple[TrendPoint, ...]:
    """Para cada día, la suma del último recuento conocido de cada repositorio al final de ese
    día (UTC). Un repositorio que aún no tenía escaneo completado no suma nada."""
    ordered = sorted(points, key=lambda p: p.finished_at)
    latest: dict[RepositoryId, SeverityCounts] = {}
    series: list[TrendPoint] = []
    index = 0
    for offset in range(days - 1, -1, -1):
        day = last_day - timedelta(days=offset)
        end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=UTC)
        while index < len(ordered) and ordered[index].finished_at < end:
            latest[ordered[index].repository_id] = ordered[index].open
            index += 1
        series.append(TrendPoint(day, sum_counts(latest.values())))
    return tuple(series)


def sum_counts(counts: Iterable[SeverityCounts]) -> SeverityCounts:
    totals = dict.fromkeys(Severity, 0)
    for item in counts:
        for severity in Severity:
            totals[severity] += item.get(severity)
    return SeverityCounts(**{s.value.lower(): n for s, n in totals.items()})
