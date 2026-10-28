// CLAUDE.md §13: lo prohibido por el estilo visual no puede llegar a la hoja de estilos.
import { describe, expect, it } from "vitest";

import { sources } from "../files";

interface Block {
  path: string;
  css: string;
}

function styleBlocks(): Block[] {
  const blocks: Block[] = [];
  for (const file of sources([".css", ".vue"])) {
    if (file.path.endsWith(".css")) {
      blocks.push({ path: file.path, css: file.text });
      continue;
    }
    for (const match of file.text.matchAll(/<style\b[^>]*>([\s\S]*?)<\/style>/g)) {
      blocks.push({ path: file.path, css: match[1] ?? "" });
    }
  }
  return blocks.map((b) => ({ ...b, css: b.css.replace(/\/\*[\s\S]*?\*\//g, "") }));
}

const FORBIDDEN: readonly [string, RegExp][] = [
  ["text-shadow", /\btext-shadow\s*:/],
  ["drop-shadow", /drop-shadow\s*\(/],
  ["degradado", /(?:linear|radial|conic)-gradient\s*\(/],
  ["animaciones", /@keyframes|\banimation(?:-[a-z]+)?\s*:/],
  ["desenfoque", /backdrop-filter|filter\s*:\s*blur/],
  ["!important", /!important/],
  ["opacidad decorativa", /\bopacity\s*:\s*0?\.\d/],
];

describe("estilos", () => {
  const blocks = styleBlocks();

  it("hay hojas de estilo que comprobar", () => {
    expect(blocks.length).toBeGreaterThan(1);
  });

  it.each(FORBIDDEN)("no usan %s", (_name, pattern) => {
    expect(blocks.filter((b) => pattern.test(b.css)).map((b) => b.path)).toEqual([]);
  });

  it("border-radius y box-shadow salen siempre de tokens (ADR 0023)", () => {
    const bad: string[] = [];
    for (const b of blocks) {
      for (const m of b.css.matchAll(/(border(?:-[a-z]+)*-radius|box-shadow)\s*:\s*([^;}]+)/g)) {
        const value = (m[2] ?? "").trim();
        const allowed =
          value === "0" ||
          value === "none" ||
          /^var\(--(?:radius|radius-sm|radius-pill|shadow-sm|shadow-md)\)$/.test(value) ||
          /^inset 3px 0 0 var\(--tone-color, transparent\)$/.test(value);
        if (!allowed) bad.push(`${b.path}: ${m[1] ?? ""}: ${value}`);
      }
    }
    expect(bad).toEqual([]);
  });

  it("los colores salen de los tokens: ni hex ni rgb() ni hsl() fuera de tokens.css", () => {
    const bad = blocks
      .filter((b) => !b.path.endsWith("tokens.css"))
      .filter((b) => /#[0-9a-fA-F]{3,8}\b|\b(?:rgba?|hsla?|hwb|oklch|lab|lch)\s*\(/.test(b.css))
      .map((b) => b.path);
    expect(bad).toEqual([]);
  });

  it("tokens.css define el contrato del ADR 0023 (severidades, radios, sombras, tema oscuro)", () => {
    const tokens = blocks.find((b) => b.path.endsWith("tokens.css"))?.css ?? "";
    for (const name of ["critical", "high", "medium", "low", "info"]) {
      expect(tokens).toContain(`--sev-${name}:`);
      expect(tokens).toContain(`--sev-${name}-bg:`);
    }
    for (const name of ["--radius", "--radius-sm", "--radius-pill", "--shadow-sm", "--shadow-md", "--accent"]) {
      expect(tokens).toContain(`${name}:`);
    }
    expect(tokens).toContain("prefers-color-scheme: dark");
  });

  it("no usa clases utilitarias de Tailwind en plantillas", () => {
    const offenders = sources([".vue"])
      .filter((f) => /class="[^"]*\b(?:flex|p-\d|m-\d|text-(?:sm|lg|xl)|bg-[a-z]+-\d{3}|rounded|shadow)\b/.test(f.text))
      .map((f) => f.path);
    expect(offenders).toEqual([]);
  });
});
