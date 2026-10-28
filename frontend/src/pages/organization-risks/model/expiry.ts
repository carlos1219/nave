import type { StatusTone } from "@/shared/ui";

const DAY_MS = 86_400_000;
const SOON_DAYS = 14;

/** Estado de caducidad de un riesgo aceptado respecto a `now`. */
export function expiryView(iso: string | null, now: number): { label: string; tone: StatusTone } {
  const at = iso === null ? Number.NaN : Date.parse(iso);
  if (!Number.isFinite(at)) return { label: "Sin fecha", tone: "neutral" };
  const days = Math.ceil((at - now) / DAY_MS);
  if (days <= 0) return { label: "Caducado", tone: "fail" };
  if (days === 1) return { label: "Caduca mañana", tone: "warn" };
  return { label: `Caduca en ${String(days)} días`, tone: days <= SOON_DAYS ? "warn" : "neutral" };
}
