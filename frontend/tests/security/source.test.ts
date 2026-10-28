// AM-701, AM-705: ninguna vía de HTML dinámico, de evaluación de código ni de almacenamiento
// del navegador. ESLint lo exige también; este test no depende de su configuración.
import { describe, expect, it } from "vitest";

import { sources } from "../files";

const FORBIDDEN: readonly [string, RegExp][] = [
  ["v-html", /\bv-html\b/],
  ["innerHTML", /\binnerHTML\b/],
  ["outerHTML", /\bouterHTML\b/],
  ["insertAdjacentHTML", /\binsertAdjacentHTML\b/],
  ["document.write", /\bdocument\s*\.\s*write(ln)?\b/],
  ["createContextualFragment", /\bcreateContextualFragment\b/],
  ["eval", /\beval\s*\(/],
  ["new Function", /\bnew\s+Function\b/],
  ["setTimeout con texto", /\bset(Timeout|Interval)\s*\(\s*["'`]/],
  ["localStorage", /\blocalStorage\b/],
  ["sessionStorage", /\bsessionStorage\b/],
  ["indexedDB", /\bindexedDB\b/],
  ["h() con innerHTML", /domProps/],
  ["javascript: URL", /javascript:/i],
  ["data: URL en atributos", /(?:href|src)\s*=\s*["']data:/i],
];

describe("código fuente", () => {
  const files = sources([".ts", ".vue"]).filter((f) => !f.path.endsWith(".d.ts"));

  it.each(FORBIDDEN)("no usa %s", (_name, pattern) => {
    const offenders = files.filter((f) => pattern.test(f.text)).map((f) => f.path);
    expect(offenders).toEqual([]);
  });

  it("no hace fetch fuera del cliente API", () => {
    const offenders = files
      .filter((f) => /\bfetch\s*\(/.test(f.text) && !f.path.startsWith("src/shared/api/"))
      .map((f) => f.path);
    expect(offenders).toEqual([]);
  });

  it("no incluye URLs absolutas externas", () => {
    const offenders = files.filter((f) => /https?:\/\/(?!localhost|127\.0\.0\.1)/.test(f.text));
    expect(offenders.map((f) => f.path)).toEqual([]);
  });

  it("solo la página de entrada y las descargas usan :href y ningún enlace abre ventanas nuevas", () => {
    // Las descargas (ADR 0029) usan URLs de `shared/api` construidas con UUID validados y
    // valores de listas cerradas: ningún texto del servidor llega al atributo.
    const withHref = files.filter((f) => /:href\s*=/.test(f.text)).map((f) => f.path);
    expect(withHref).toEqual(["src/features/export-findings/ui/ExportLinks.vue", "src/pages/login/ui/LoginPage.vue"]);
    expect(files.filter((f) => /\btarget\s*=/.test(f.text)).map((f) => f.path)).toEqual([]);
    expect(files.filter((f) => /window\s*\.\s*open\b/.test(f.text)).map((f) => f.path)).toEqual([]);
  });

  it("todo enlace con identificadores del servidor comprueba que son UUID", () => {
    const risky = files.filter(
      (f) => f.path.endsWith(".vue") && /params:\s*\{[^}]*\b(row|org|repo|scan)\.[a-z_]*id\b/.test(f.text),
    );
    expect(risky.length).toBeGreaterThan(0);
    expect(risky.filter((f) => !f.text.includes("isUuid(")).map((f) => f.path)).toEqual([]);
  });
});
