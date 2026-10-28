const ISO = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})/;

/** `2026-10-12T17:41:05Z` → `2026-10-12 17:41:05`. Determinista (no depende de la zona ni del idioma). */
export function formatTimestamp(iso: string): string {
  const match = ISO.exec(iso);
  if (match === null) return "—";
  const [, y, mo, d, h, mi, s] = match;
  return `${y ?? ""}-${mo ?? ""}-${d ?? ""} ${h ?? ""}:${mi ?? ""}:${s ?? ""}`;
}

/** Duración entre dos instantes ISO: `1 min 30 s`, `45 s`, `2 h 05 min`. `—` si falta alguno o no cuadra. */
export function formatDuration(startIso: string | null, endIso: string | null): string {
  if (startIso === null || endIso === null) return "—";
  const ms = Date.parse(endIso) - Date.parse(startIso);
  if (!Number.isFinite(ms) || ms < 0) return "—";
  const total = Math.round(ms / 1000);
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const seconds = total % 60;
  if (hours > 0) return `${String(hours)} h ${String(minutes).padStart(2, "0")} min`;
  if (minutes > 0) return `${String(minutes)} min ${String(seconds).padStart(2, "0")} s`;
  return `${String(seconds)} s`;
}
