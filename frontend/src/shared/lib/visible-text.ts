/**
 * Hace seguro de leer un texto controlado por un atacante (AM-703, AM-704): los caracteres de
 * control, las secuencias ANSI, los de dirección bidireccional y los de ancho cero se muestran
 * como `<U+XXXX>` en lugar de actuar. El resultado se muestra siempre como texto, nunca como HTML.
 *
 * Los rangos se declaran como números y la expresión se construye en tiempo de carga, para que
 * ningún carácter invisible viva en el código fuente.
 */
const ESC = 0x1b;
const BEL = 0x07;

// [desde, hasta] inclusive.
const HIDDEN_RANGES: readonly (readonly [number, number])[] = [
  [0x00, 0x08], // C0 salvo tabulador (09) y salto de línea (0A)
  [0x0b, 0x1f],
  [0x7f, 0x9f], // DEL y C1
  [0xad, 0xad], // guion blando
  [0x061c, 0x061c], // marca de letra árabe
  [0x180e, 0x180e],
  [0x200b, 0x200f], // ancho cero y marcas de dirección
  [0x2028, 0x202e], // separadores de línea y párrafo, y controles bidi
  [0x2060, 0x206f], // unión de palabras, controles bidi aislados e invisibles
  [0xfeff, 0xfeff], // BOM / espacio de ancho cero
  [0xfff9, 0xfffb], // anotaciones interlineales
];

function hex(code: number): string {
  return code.toString(16).padStart(4, "0");
}

const HIDDEN = new RegExp(
  `[${HIDDEN_RANGES.map(([from, to]) => `\\u${hex(from)}-\\u${hex(to)}`).join("")}]`,
  "g",
);
// CSI (ESC [ ... final) y OSC (ESC ] ... BEL | ESC \).
const ANSI = new RegExp(
  `${String.fromCharCode(ESC)}\\[[0-9;?]*[ -/]*[@-~]|${String.fromCharCode(ESC)}\\][^${String.fromCharCode(BEL)}${String.fromCharCode(ESC)}]*(?:${String.fromCharCode(BEL)}|${String.fromCharCode(ESC)}\\\\)?`,
  "g",
);

function marker(char: string): string {
  return `<U+${(char.codePointAt(0) ?? 0).toString(16).toUpperCase().padStart(4, "0")}>`;
}

export function visibleText(value: string, maxLength = 2000): string {
  const clipped = value.length > maxLength ? `${value.slice(0, maxLength)}…[recortado]` : value;
  return clipped.replace(ANSI, "").replace(HIDDEN, marker);
}
