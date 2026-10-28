import type { TriageRequest, TriageState } from "@/shared/api";

export const MIN_JUSTIFICATION = 10;
export const MAX_JUSTIFICATION = 2000;
const NEEDS_JUSTIFICATION: readonly TriageState[] = ["FALSE_POSITIVE", "ACCEPTED_RISK"];

export interface TriageDraft {
  state: TriageState;
  justification: string;
  /** Valor de `datetime-local` (hora local, sin zona) o vacío. */
  expiresLocal: string;
}

export type TriageResult = { ok: true; request: TriageRequest } | { ok: false; message: string };

/** Refleja las reglas del servidor para avisar pronto; la validación real sigue siendo la suya. */
export function buildTriageRequest(draft: TriageDraft, now: Date = new Date()): TriageResult {
  const justification = draft.justification.trim();
  const needsText = NEEDS_JUSTIFICATION.includes(draft.state);
  if (needsText && justification.length < MIN_JUSTIFICATION) {
    return { ok: false, message: `La justificación necesita al menos ${String(MIN_JUSTIFICATION)} caracteres.` };
  }
  if (justification.length > MAX_JUSTIFICATION) {
    return { ok: false, message: "La justificación es demasiado larga." };
  }
  let expiresAt: string | null = null;
  if (draft.state === "ACCEPTED_RISK") {
    const when = new Date(draft.expiresLocal);
    if (draft.expiresLocal === "" || Number.isNaN(when.getTime())) {
      return { ok: false, message: "El riesgo aceptado necesita una fecha de caducidad." };
    }
    if (when.getTime() <= now.getTime()) {
      return { ok: false, message: "La fecha de caducidad debe estar en el futuro." };
    }
    expiresAt = when.toISOString();
  }
  return {
    ok: true,
    request: { state: draft.state, justification: justification === "" ? null : justification, expires_at: expiresAt },
  };
}
