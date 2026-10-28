// AM-705: la CSP de producción no permitirá nada en línea; el HTML de entrada debe cumplirla.
import { describe, expect, it } from "vitest";

import { read } from "../files";

describe("index.html", () => {
  const html = read("index.html");

  it("no tiene scripts en línea, manejadores ni estilos en línea", () => {
    const inlineScript = /<script(?![^>]*\bsrc=)[^>]*>/i;
    expect(html).not.toMatch(inlineScript);
    expect(html).not.toMatch(/\son[a-z]+\s*=/i);
    expect(html).not.toMatch(/<style\b/i);
    expect(html).not.toMatch(/\sstyle\s*=/i);
  });

  it("no carga nada de otros orígenes", () => {
    expect(html).not.toMatch(/(?:src|href)\s*=\s*["'](?:https?:)?\/\//i);
  });

  it("no envía el Referer", () => {
    expect(html).toMatch(/name="referrer"\s+content="no-referrer"/);
  });
});
