import { describe, expect, it } from "vitest";
import { ref } from "vue";

import { niceMax, stackedAreas, stackSegments, useTextFilter, yTicks } from "@/shared/lib";
import type { Box } from "@/shared/lib";

const BOX: Box = { width: 110, height: 70, left: 10, right: 0, top: 10, bottom: 10 };

describe("geometría de gráficas", () => {
  it("niceMax redondea hacia arriba a 1, 2 o 5 por potencia de diez", () => {
    expect([0, 1, 3, 7, 10, 11, 46, 120, Number.NaN].map(niceMax)).toEqual([1, 1, 5, 10, 10, 20, 50, 200, 1]);
  });

  it("yTicks reparte el eje en pasos enteros iguales", () => {
    expect(yTicks(10, BOX).map((t) => t.value)).toEqual([0, 2, 4, 6, 8, 10]);
    expect(yTicks(2, BOX).map((t) => t.value)).toEqual([0, 1, 2]);
  });

  it("yTicks pone el cero abajo y el máximo arriba, sin repetir valores", () => {
    expect(yTicks(4, BOX)).toEqual([
      { value: 0, y: 60 },
      { value: 1, y: 47.5 },
      { value: 2, y: 35 },
      { value: 3, y: 22.5 },
      { value: 4, y: 10 },
    ]);
    expect(yTicks(1, BOX).map((t) => t.value)).toEqual([0, 1]);
  });

  it("stackedAreas apila cada serie sobre la anterior", () => {
    const bands = stackedAreas([{ a: 1, b: 1 }, { a: 2, b: 0 }], ["a", "b"], BOX, 2);
    expect(bands.map((b) => b.key)).toEqual(["a", "b"]);
    expect(bands[0]?.path).toBe("M10,35L110,10L110,60L10,60Z");
    expect(bands[1]?.path).toBe("M10,10L110,10L110,10L10,35Z");
    expect(stackedAreas([], ["a"], BOX, 1)).toEqual([]);
  });

  it("stackedAreas no se sale del área ni con valores negativos o por encima del máximo", () => {
    const [band] = stackedAreas([{ a: -5 }, { a: 99 }], ["a"], BOX, 2);
    expect(band?.path).toBe("M10,60L110,10L110,60L10,60Z");
  });

  it("stackSegments omite los ceros y escala respecto al máximo común", () => {
    expect(stackSegments([{ key: "x", value: 1 }, { key: "y", value: 0 }, { key: "z", value: 2 }], 100, 4)).toEqual([
      { key: "x", x: 0, width: 25 },
      { key: "z", x: 25, width: 50 },
    ]);
    expect(stackSegments([{ key: "x", value: 3 }], 100, 0)).toEqual([{ key: "x", x: 0, width: 0 }]);
  });
});

describe("useTextFilter", () => {
  it("filtra sin distinguir mayúsculas y vacío devuelve todo", () => {
    const rows = ref(["Semgrep.SQL", "trivy-CVE"]);
    const f = useTextFilter(() => rows.value, (r) => [r]);
    expect(f.filtered.value).toEqual(rows.value);
    f.query.value = "  sql ";
    expect(f.filtered.value).toEqual(["Semgrep.SQL"]);
    f.query.value = "nada";
    expect(f.filtered.value).toEqual([]);
  });
});
