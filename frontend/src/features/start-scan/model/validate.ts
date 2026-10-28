const COMMIT = /^[0-9a-f]{40}$/i;
const REF = /^[A-Za-z0-9][A-Za-z0-9._/-]{0,199}$/;

export function validateCommit(value: string): string | null {
  return COMMIT.test(value) ? null : "El commit debe ser un SHA-1 completo de 40 caracteres hexadecimales.";
}

/** Subconjunto conservador de `git check-ref-format`; el servidor valida igualmente. */
export function validateRef(value: string): string | null {
  const bad =
    !REF.test(value) ||
    value.includes("..") ||
    value.includes("//") ||
    value.endsWith("/") ||
    value.endsWith(".") ||
    value.endsWith(".lock") ||
    value.includes("/.");
  return bad ? "La referencia no es válida (letras, números, '.', '_', '-' y '/')." : null;
}
