import { describe, expect, it } from "vitest";

import {
  emptyFilter,
  parseFilter,
  serializeFilter,
  toApiFilter,
  useFindingsFilter,
} from "@/features/findings-filter";

const ID = "0F8FAD5B-D9CB-469F-A165-70867728950E";

describe("parseFilter", () => {
  it("descarta todo lo que no está en las listas cerradas", () => {
    const f = parseFilter({
      severity: ["CRITICAL", "<script>", "high", "__proto__"],
      state: ["OPEN", "x"],
      scanner: ["semgrep", "Bad Name", "a", "x'; DROP", "semgrep"],
      only_new: "yes",
      seen_in_scan: "no-es-uuid",
    });
    expect(f).toEqual({ severity: ["CRITICAL"], state: ["OPEN"], scanner: ["semgrep"], onlyNew: false, seenInScan: null });
  });

  it("acepta un valor único, normaliza el UUID y limita escáneres", () => {
    const many = Array.from({ length: 20 }, (_, i) => `scan-${String(i)}x`);
    const f = parseFilter({ severity: "HIGH", only_new: "true", seen_in_scan: ID, scanner: many });
    expect(f.severity).toEqual(["HIGH"]);
    expect(f.onlyNew).toBe(true);
    expect(f.seenInScan).toBe(ID.toLowerCase());
    expect(f.scanner).toHaveLength(8);
  });

  it("consulta vacía da filtro vacío y ida y vuelta es estable", () => {
    expect(parseFilter({})).toEqual(emptyFilter());
    const original = { severity: ["LOW" as const], state: [], scanner: ["gitleaks"], onlyNew: true, seenInScan: ID.toLowerCase() };
    expect(parseFilter(serializeFilter(original))).toEqual(original);
  });
});

describe("toApiFilter / useFindingsFilter", () => {
  it("solo incluye lo activo", () => {
    expect(toApiFilter(emptyFilter())).toEqual({});
    expect(toApiFilter({ ...emptyFilter(), onlyNew: true, severity: ["HIGH"] })).toEqual({ onlyNew: true, severity: ["HIGH"] });
  });

  it("alterna, rechaza escáneres inválidos y se limpia", () => {
    const f = useFindingsFilter();
    expect(f.active.value).toBe(false);
    f.toggleSeverity("HIGH");
    f.toggleSeverity("LOW");
    f.toggleSeverity("HIGH");
    f.toggleScanner("bad name");
    f.toggleScanner("semgrep");
    f.setOnlyNew(true);
    expect(f.apiFilter.value).toEqual({ severity: ["LOW"], scanner: ["semgrep"], onlyNew: true });
    expect(f.active.value).toBe(true);
    f.reset();
    expect(f.apiFilter.value).toEqual({});
  });
});
