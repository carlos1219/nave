"""Comentario de resumen en PRs. Escapa todo texto procedente de findings (AM-802).

Cada texto controlado por el atacante (título, regla, ruta) se emite dentro de un
code span de CommonMark cuyo delimitador es más largo que cualquier racha de
comillas invertidas del propio texto. Dentro de un code span no hay menciones,
enlaces, imágenes ni HTML, y el texto no puede cerrar el span antes de tiempo.
Se usan listas y no tablas: en GFM un `|` dentro de un code span parte la celda.
"""

import re
from collections.abc import Sequence
from typing import Final

from nave.application.dto.vcs import MAX_COMMENT_LENGTH, PR_COMMENT_MARKER, PrCommentBody
from nave.application.dto.views import FindingView
from nave.domain.quality_gates import PolicyVerdict, VerdictReason
from nave.domain.value_objects.scan_summary import ScanSummary
from nave.domain.value_objects.severity import Severity

MAX_LISTED_FINDINGS: Final = 20
MAX_INLINE_TEXT: Final = 200
_BACKTICK_RUN: Final = re.compile(r"`+")


def code_span(text: str, limit: int = MAX_INLINE_TEXT) -> str:
    collapsed = " ".join(text.split())[:limit] or "-"
    longest = max((len(run) for run in _BACKTICK_RUN.findall(collapsed)), default=0)
    fence = "`" * (longest + 1)
    # Espacio a ambos lados: CommonMark elimina uno de cada lado, y así un texto que
    # empieza o termina por "`" no se funde con el delimitador.
    return f"{fence} {collapsed} {fence}"


def _counts_line(summary: ScanSummary) -> str:
    return " | ".join(f"{s.value} {summary.new.get(s)}" for s in sorted(Severity, reverse=True))


def _reason_line(reason: VerdictReason) -> str:
    severity = reason.severity.value if reason.severity else "-"
    flag = "bloqueante" if reason.blocking else "informativo"
    return (
        f"- {code_span(reason.rule)} {severity}: {reason.observed} (límite {reason.limit}, {flag})"
    )


def _finding_line(view: FindingView) -> str:
    finding = view.finding
    where = "-"
    if view.occurrence is not None and view.occurrence.location is not None:
        location = view.occurrence.location
        where = location.path.value
        if location.start_line is not None:
            where = f"{where}:{location.start_line}"
    return (
        f"- **{finding.severity.value}** {code_span(finding.rule_id.value)} en "
        f"{code_span(where)}: {code_span(finding.title.value)}"
    )


def build_pr_comment(
    *,
    summary: ScanSummary,
    verdict: PolicyVerdict,
    new_findings: Sequence[FindingView],
) -> PrCommentBody:
    lines = [
        PR_COMMENT_MARKER,
        "### NAVE: resultado del análisis",
        "",
        f"**Veredicto:** {verdict.verdict.value}",
        f"**Hallazgos nuevos:** {_counts_line(summary)}",
    ]
    if summary.truncated:
        lines.append("**Aviso:** resultados incompletos; el veredicto no puede ser PASS.")
    if verdict.reasons:
        lines += ["", "#### Motivos", *(_reason_line(r) for r in verdict.reasons)]
    listed = list(new_findings[:MAX_LISTED_FINDINGS])
    if listed:
        lines += ["", "#### Hallazgos nuevos", *(_finding_line(v) for v in listed)]
        remaining = summary.new.total - len(listed)
        if remaining > 0:
            lines.append(f"- ... y {remaining} más.")
    markdown = "\n".join(lines)
    if len(markdown) > MAX_COMMENT_LENGTH:
        markdown = markdown[: MAX_COMMENT_LENGTH - 32].rsplit("\n", 1)[0] + "\n- ... (recortado)"
    return PrCommentBody(markdown)
