import type { WebhookCounter } from "@/shared/api";

export const REASONS = ["SIGNATURE", "MEDIA_TYPE", "MALFORMED", "DUPLICATE"] as const;
export type Reason = (typeof REASONS)[number];

export const REASON_LABELS: Readonly<Record<Reason, string>> = {
  SIGNATURE: "Firma no válida",
  MEDIA_TYPE: "Tipo de contenido",
  MALFORMED: "Cuerpo o cabeceras no válidos",
  DUPLICATE: "Duplicados",
};

export interface HourRow {
  hour: string;
  counts: Record<Reason, number>;
  total: number;
}

const empty = (): Record<Reason, number> => ({ SIGNATURE: 0, MEDIA_TYPE: 0, MALFORMED: 0, DUPLICATE: 0 });

/** Totales por motivo de las últimas 24 h. */
export function totals(counters: readonly WebhookCounter[]): Record<Reason, number> {
  const out = empty();
  for (const c of counters) out[c.reason] += c.count;
  return out;
}

/** Agrupa los contadores por hora (UTC), la más reciente primero; solo horas con actividad. */
export function byHour(counters: readonly WebhookCounter[]): HourRow[] {
  const rows = new Map<string, Record<Reason, number>>();
  for (const c of counters) {
    const hour = `${c.minute.slice(0, 13)}:00`;
    const counts = rows.get(hour) ?? empty();
    counts[c.reason] += c.count;
    rows.set(hour, counts);
  }
  return [...rows.entries()]
    .sort(([a], [b]) => (a < b ? 1 : -1))
    .map(([hour, counts]) => ({ hour, counts, total: REASONS.reduce((sum, r) => sum + counts[r], 0) }));
}
