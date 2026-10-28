const DAY_MS = 86_400_000;

/** Antigüedad de un hallazgo abierto en días enteros; texto vacío si la fecha no es válida. */
export function ageLabel(firstSeenAt: string, now: number): string {
  const since = Date.parse(firstSeenAt);
  if (Number.isNaN(since)) return "—";
  const days = Math.max(0, Math.floor((now - since) / DAY_MS));
  if (days === 0) return "hoy";
  return days === 1 ? "1 día" : `${String(days)} días`;
}
