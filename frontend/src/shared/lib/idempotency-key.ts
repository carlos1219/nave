/**
 * Clave de idempotencia de 128 bits aleatorios en hexadecimal (32 caracteres, dentro de lo que
 * acepta el cliente API). `getRandomValues` funciona también fuera de contextos seguros, a
 * diferencia de `randomUUID`.
 */
export function newIdempotencyKey(): string {
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  return Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
}
