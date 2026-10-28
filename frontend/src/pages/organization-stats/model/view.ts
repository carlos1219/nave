import { SEVERITIES, toneOf, totalCount } from "@/entities/finding";
import type { OrganizationStats, SeverityCounts } from "@/shared/api";
import type { Tone } from "@/shared/ui";

export const SERIES: readonly { key: Tone; label: string }[] = SEVERITIES.map((s) => ({ key: toneOf(s), label: s }));

export interface Kpi {
  label: string;
  value: string;
  detail: string;
}

/** `2026-10-25` → `25/10`: etiqueta corta y sin depender del idioma del navegador. */
export function shortDay(iso: string): string {
  const match = /^\d{4}-(\d{2})-(\d{2})$/.exec(iso);
  return match ? `${match[2] ?? ""}/${match[1] ?? ""}` : "";
}

export function trendPoints(stats: OrganizationStats): { label: string; values: SeverityCounts }[] {
  return stats.trend.map((p) => ({ label: shortDay(p.day), values: p.open }));
}

/** Media de días de corrección ponderada por el número de hallazgos corregidos. */
export function meanFixDays(stats: OrganizationStats): number | null {
  const fixed = stats.fix_times.reduce((sum, f) => sum + f.fixed, 0);
  if (fixed === 0) return null;
  return stats.fix_times.reduce((sum, f) => sum + f.mean_days * f.fixed, 0) / fixed;
}

export function formatDays(days: number): string {
  return days < 1 ? "< 1 día" : `${days.toFixed(1).replace(".", ",")} días`;
}

export function kpis(stats: OrganizationStats): Kpi[] {
  const open = totalCount(stats.open);
  const severe = stats.open.critical + stats.open.high;
  const first = stats.trend[0];
  const change = first === undefined ? 0 : open - totalCount(first.open);
  const overdue = stats.sla.reduce((sum, s) => sum + s.overdue, 0);
  const fix = meanFixDays(stats);
  return [
    { label: "Abiertas", value: String(open), detail: `${change > 0 ? "+" : ""}${String(change)} en 30 días` },
    { label: "Críticas y altas", value: String(severe), detail: `${String(stats.open.critical)} CRITICAL, ${String(stats.open.high)} HIGH` },
    {
      label: "Fuera de plazo",
      value: stats.sla.length === 0 ? "—" : String(overdue),
      detail: stats.sla.length === 0 ? "La política no define plazos" : "Según los plazos de la política",
    },
    {
      label: "Tiempo medio de corrección",
      value: fix === null ? "—" : formatDays(fix),
      detail: fix === null ? "Aún no hay hallazgos corregidos" : "De la primera a la última detección",
    },
  ];
}

/** Máximo común de una lista de recuentos: todas las barras usan la misma escala. */
export function maxTotal(rows: readonly { open: SeverityCounts }[]): number {
  return Math.max(1, ...rows.map((r) => totalCount(r.open)));
}
