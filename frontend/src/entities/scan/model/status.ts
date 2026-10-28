import { visibleText } from "@/shared/lib";
import type { StatusTone } from "@/shared/ui";

const LABELS: Readonly<Record<string, string>> = {
  QUEUED: "En cola",
  FETCHING: "Descargando",
  SCANNING: "Escaneando",
  PROCESSING: "Procesando",
  COMPLETED: "Completado",
  FAILED: "Fallido",
  CANCELLED: "Cancelado",
  TIMED_OUT: "Tiempo agotado",
};
const TERMINAL: ReadonlySet<string> = new Set(["COMPLETED", "FAILED", "CANCELLED", "TIMED_OUT"]);
const TONES: Readonly<Record<string, StatusTone>> = {
  COMPLETED: "ok",
  FAILED: "fail",
  TIMED_OUT: "fail",
  CANCELLED: "neutral",
};
const VERDICTS: Readonly<Record<string, { label: string; tone: StatusTone }>> = {
  PASS: { label: "PASS", tone: "ok" },
  FAIL: { label: "FAIL", tone: "fail" },
  NEUTRAL: { label: "NEUTRAL", tone: "warn" },
};

export function isTerminalStatus(status: string): boolean {
  return TERMINAL.has(status);
}

/** Un escaneo en curso o en cola se puede cancelar; uno terminado no. */
export function isCancellable(status: string): boolean {
  return Object.hasOwn(LABELS, status) && !TERMINAL.has(status);
}

/** El estado viene del servidor como texto abierto: lo desconocido se muestra acotado y visible. */
export function statusLabel(status: string): string {
  return Object.hasOwn(LABELS, status) ? (LABELS[status] ?? status) : visibleText(status, 32);
}

/** Lo que no ha terminado está en curso; lo desconocido es neutro. */
export function statusTone(status: string): StatusTone {
  if (Object.hasOwn(TONES, status)) return TONES[status] ?? "neutral";
  return isCancellable(status) ? "running" : "neutral";
}

const REASONS: Readonly<Record<string, string>> = {
  FETCH_FAILED: "No se pudo descargar el código",
  REPOSITORY_TOO_LARGE: "Repositorio demasiado grande",
  SCANNER_FAILED: "El escáner terminó con error",
  SCANNER_OUTPUT_INVALID: "Salida del escáner no válida",
  PROCESSING_FAILED: "Error al procesar los resultados",
  INTERNAL_ERROR: "Error interno",
  TIMEOUT: "Superó el tiempo máximo",
  QUEUE_EXPIRED: "Caducó en la cola",
  USER_CANCELLED: "Cancelado por un usuario",
  SUPERSEDED: "Sustituido por un escaneo posterior",
};

/** Causa de un estado terminal no exitoso, en palabras; lo desconocido se muestra acotado. */
export function reasonLabel(code: string): string {
  return Object.hasOwn(REASONS, code) ? (REASONS[code] ?? code) : visibleText(code, 32);
}

/** Veredicto de quality gate; `null` cuando aún no hay o no se evaluó. */
export function verdictView(verdict: string | null | undefined): { label: string; tone: StatusTone } | null {
  if (verdict === null || verdict === undefined) return null;
  return Object.hasOwn(VERDICTS, verdict)
    ? (VERDICTS[verdict] ?? null)
    : { label: visibleText(verdict, 16), tone: "neutral" };
}
