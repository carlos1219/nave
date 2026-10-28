import { describe, expect, it } from "vitest";

import { nonZeroCounts, totalCount, worstTone } from "@/entities/finding";
import { canAdminister, canTriage, hasRole } from "@/entities/organization";
import { isCancellable, isTerminalStatus, statusLabel, statusTone, verdictView } from "@/entities/scan";

describe("estado de escaneo", () => {
  it.each(["COMPLETED", "FAILED", "CANCELLED", "TIMED_OUT"])("%s es terminal y no cancelable", (s) => {
    expect(isTerminalStatus(s)).toBe(true);
    expect(isCancellable(s)).toBe(false);
  });

  it.each(["QUEUED", "FETCHING", "SCANNING", "PROCESSING"])("%s es cancelable", (s) => {
    expect(isTerminalStatus(s)).toBe(false);
    expect(isCancellable(s)).toBe(true);
  });

  it("un estado desconocido ni es terminal ni cancelable, y se muestra acotado y visible", () => {
    for (const hostile of ["__proto__", "constructor", "toString", "X‮evil"]) {
      expect(isCancellable(hostile)).toBe(false);
      expect(isTerminalStatus(hostile)).toBe(false);
    }
    expect(statusLabel("__proto__")).toBe("__proto__");
    expect(statusLabel("A‮B")).not.toContain("‮");
    expect(statusLabel("x".repeat(500)).length).toBeLessThan(64);
    expect(statusLabel("COMPLETED")).toBe("Completado");
  });

  it("asigna tono: en curso, correcto, fallido o neutro (lo desconocido es neutro)", () => {
    expect(statusTone("SCANNING")).toBe("running");
    expect(statusTone("COMPLETED")).toBe("ok");
    expect(statusTone("TIMED_OUT")).toBe("fail");
    expect(statusTone("CANCELLED")).toBe("neutral");
    expect(statusTone("__proto__")).toBe("neutral");
  });

  it("el veredicto lleva texto propio y lo desconocido se muestra acotado", () => {
    expect(verdictView("FAIL")).toEqual({ label: "FAIL", tone: "fail" });
    expect(verdictView("NEUTRAL")?.tone).toBe("warn");
    expect(verdictView(null)).toBeNull();
    expect(verdictView(undefined)).toBeNull();
    expect(verdictView("constructor")).toEqual({ label: "constructor", tone: "neutral" });
    expect(verdictView("A‮B")?.label).not.toContain("‮");
  });
});

describe("recuentos de vulnerabilidades abiertas", () => {
  const counts = { critical: 0, high: 2, medium: 0, low: 1, info: 0 };

  it("lista solo las severidades presentes, de mayor a menor, y suma el total", () => {
    expect(nonZeroCounts(counts)).toEqual([
      { severity: "HIGH", count: 2 },
      { severity: "LOW", count: 1 },
    ]);
    expect(totalCount(counts)).toBe(3);
  });

  it("el tono es el de la peor severidad, o ninguno", () => {
    expect(worstTone(counts)).toBe("high");
    expect(worstTone({ critical: 0, high: 0, medium: 0, low: 0, info: 0 })).toBeUndefined();
  });
});

describe("roles", () => {
  it("jerarquía VIEWER < TRIAGER < ADMIN", () => {
    expect(hasRole("VIEWER", "TRIAGER")).toBe(false);
    expect(hasRole("TRIAGER", "TRIAGER")).toBe(true);
    expect(hasRole("ADMIN", "TRIAGER")).toBe(true);
    expect(canTriage("VIEWER")).toBe(false);
    expect(canTriage("TRIAGER")).toBe(true);
    expect(canAdminister("TRIAGER")).toBe(false);
    expect(canAdminister("ADMIN")).toBe(true);
  });
});
