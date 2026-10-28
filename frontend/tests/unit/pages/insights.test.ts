import { describe, expect, it } from "vitest";

import { expiryView } from "@/pages/organization-risks/model/expiry";
import { formatDays, kpis, meanFixDays, shortDay, trendPoints } from "@/pages/organization-stats/model/view";
import type { OrganizationStats } from "@/shared/api";

const NOW = Date.parse("2026-10-26T12:00:00Z");
const zero = { critical: 0, high: 0, medium: 0, low: 0, info: 0 };

describe("caducidad de riesgos aceptados", () => {
  it.each([
    ["2026-10-20T00:00:00Z", "Caducado", "fail"],
    ["2026-10-27T10:00:00Z", "Caduca mañana", "warn"],
    ["2026-11-05T12:00:00Z", "Caduca en 10 días", "warn"],
    ["2027-01-01T00:00:00Z", "Caduca en 67 días", "neutral"],
    [null, "Sin fecha", "neutral"],
    ["no es fecha", "Sin fecha", "neutral"],
  ])("%s → %s", (iso, label, tone) => {
    expect(expiryView(iso, NOW)).toEqual({ label, tone });
  });
});

describe("vista de estadísticas", () => {
  const stats: OrganizationStats = {
    open: { ...zero, critical: 1, high: 2, low: 4 },
    by_scanner: [],
    by_repository: [],
    top_rules: [],
    fix_times: [
      { severity: "HIGH", fixed: 1, mean_days: 2 },
      { severity: "LOW", fixed: 3, mean_days: 6 },
    ],
    sla: [{ severity: "HIGH", days: 30, overdue: 2 }],
    trend: [
      { day: "2026-09-27", open: { ...zero, high: 3 } },
      { day: "2026-10-26", open: { ...zero, critical: 1, high: 2, low: 4 } },
    ],
  };

  it("resume las cifras clave con su contexto", () => {
    expect(kpis(stats).map((k) => [k.label, k.value, k.detail])).toEqual([
      ["Abiertas", "7", "+4 en 30 días"],
      ["Críticas y altas", "3", "1 CRITICAL, 2 HIGH"],
      ["Fuera de plazo", "2", "Según los plazos de la política"],
      ["Tiempo medio de corrección", "5,0 días", "De la primera a la última detección"],
    ]);
  });

  it("sin plazos ni correcciones lo dice en vez de inventar un número", () => {
    const empty = { ...stats, sla: [], fix_times: [] };
    const [, , sla, fix] = kpis(empty);
    expect(sla?.value).toBe("—");
    expect(fix?.value).toBe("—");
    expect(meanFixDays(empty)).toBeNull();
  });

  it("etiqueta los días de forma corta y estable", () => {
    expect(trendPoints(stats).map((p) => p.label)).toEqual(["27/09", "26/10"]);
    expect(shortDay("<script>")).toBe("");
    expect(formatDays(0.2)).toBe("< 1 día");
  });
});
