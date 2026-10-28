import { describe, expect, it } from "vitest";

import { formatDuration, formatTimestamp, visibleText } from "@/shared/lib";

const ESC = String.fromCharCode(0x1b);
const BEL = String.fromCharCode(0x07);
const c = (code: number): string => String.fromCharCode(code);

describe("visibleText (AM-703, AM-704)", () => {
  it("deja el texto normal, con tabuladores y saltos de línea, tal cual", () => {
    expect(visibleText("línea 1\n\tlínea 2 — ñ 日本")).toBe("línea 1\n\tlínea 2 — ñ 日本");
  });

  it("hace visibles los controles bidi (Trojan Source)", () => {
    expect(visibleText(`if (admin${c(0x202e)}) {`)).toBe("if (admin<U+202E>) {");
    expect(visibleText(`a${c(0x2066)}b${c(0x2069)}`)).toBe("a<U+2066>b<U+2069>");
  });

  it("hace visibles los caracteres de ancho cero y el BOM", () => {
    expect(visibleText(`pass${c(0x200b)}word${c(0xfeff)}`)).toBe("pass<U+200B>word<U+FEFF>");
  });

  it("hace visibles los controles C0 y C1 y DEL", () => {
    expect(visibleText(`a${c(0)}b${c(0x08)}c${c(0x7f)}d${c(0x85)}`)).toBe(
      "a<U+0000>b<U+0008>c<U+007F>d<U+0085>",
    );
  });

  it("elimina las secuencias ANSI (color, cursor, OSC con BEL y con ST)", () => {
    expect(visibleText(`${ESC}[31mrojo${ESC}[0m`)).toBe("rojo");
    expect(visibleText(`${ESC}[2J${ESC}[1;1Hlimpio`)).toBe("limpio");
    expect(visibleText(`${ESC}]0;título falso${BEL}texto`)).toBe("texto");
    expect(visibleText(`${ESC}]8;;https://evil.example${ESC}\\enlace`)).toBe("enlace");
  });

  it("no deja un ESC suelto activo", () => {
    expect(visibleText(`x${ESC}y`)).toBe("x<U+001B>y");
  });

  it("recorta lo largo y lo indica", () => {
    const out = visibleText("a".repeat(50), 10);
    expect(out).toBe(`${"a".repeat(10)}…[recortado]`);
  });

  it("no interpreta HTML: lo devuelve como texto", () => {
    expect(visibleText("<img src=x onerror=alert(1)>")).toBe("<img src=x onerror=alert(1)>");
  });
});

describe("formatTimestamp", () => {
  it("formatea en UTC sin depender de la zona", () => {
    expect(formatTimestamp("2026-10-12T17:41:05Z")).toBe("2026-10-12 17:41:05");
    expect(formatTimestamp("2026-10-12T17:41:05.123456+00:00")).toBe("2026-10-12 17:41:05");
  });

  it("no refleja entradas con otra forma", () => {
    expect(formatTimestamp("<script>")).toBe("—");
    expect(formatTimestamp("")).toBe("—");
  });
});

describe("formatDuration", () => {
  it("da segundos, minutos u horas con relleno fijo", () => {
    expect(formatDuration("2026-10-12T10:00:00Z", "2026-10-12T10:00:45Z")).toBe("45 s");
    expect(formatDuration("2026-10-12T10:00:00Z", "2026-10-12T10:01:30Z")).toBe("1 min 30 s");
    expect(formatDuration("2026-10-12T10:00:00Z", "2026-10-12T12:05:00Z")).toBe("2 h 05 min");
  });

  it("devuelve un guion si falta un extremo, no se entiende o va hacia atrás", () => {
    expect(formatDuration(null, "2026-10-12T10:00:00Z")).toBe("—");
    expect(formatDuration("2026-10-12T10:00:00Z", null)).toBe("—");
    expect(formatDuration("<script>", "2026-10-12T10:00:00Z")).toBe("—");
    expect(formatDuration("2026-10-12T10:00:10Z", "2026-10-12T10:00:00Z")).toBe("—");
  });
});
