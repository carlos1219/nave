import { describe, expect, it } from "vitest";

import { ClientInputError } from "@/shared/api";

import { harness, headersOf, ID_A, ID_B, json } from "./helpers";

const KEY = "0123456789abcdef0123456789abcdef";

describe("operaciones", () => {
  it("lee organizaciones, repositorios y miembros", async () => {
    const h = harness(() => json({ items: [{ id: ID_A }] }));
    expect(await h.api.organizations()).toEqual([{ id: ID_A }]);
    expect(await h.api.repositories(ID_A)).toEqual([{ id: ID_A }]);
    expect(await h.api.members(ID_A)).toEqual([{ id: ID_A }]);
    expect(h.calls.map((c) => c.url)).toEqual([
      "/api/v1/organizations",
      `/api/v1/organizations/${ID_A}/repositories`,
      `/api/v1/organizations/${ID_A}/members`,
    ]);
  });

  it("cambia y retira roles con los métodos del contrato", async () => {
    const h = harness(() => new Response(null, { status: 204 }));
    await h.api.setMemberRole(ID_A, ID_B, "TRIAGER");
    await h.api.removeMember(ID_A, ID_B);
    expect(h.calls.map((c) => [c.init.method, c.url])).toEqual([
      ["PUT", `/api/v1/organizations/${ID_A}/members/${ID_B}`],
      ["DELETE", `/api/v1/organizations/${ID_A}/members/${ID_B}`],
    ]);
    expect(h.calls[0]?.init.body).toBe('{"role":"TRIAGER"}');
  });

  it("vistas del ADR 0029: cola, sesiones y URLs de descarga validadas", async () => {
    const h = harness(() => json({ items: [], next_cursor: null }));
    await h.api.triageQueue(ID_A, { severity: ["HIGH"], repositoryId: ID_B, cursor: `4_123_${"a".repeat(32)}` });
    expect(h.calls[0]?.url).toBe(
      `/api/v1/organizations/${ID_A}/triage-queue?severity=HIGH&repository_id=${ID_B}&cursor=4_123_${"a".repeat(32)}`,
    );
    await expect(h.api.triageQueue(ID_A, { cursor: "x&limit=1" })).rejects.toBeInstanceOf(ClientInputError);
    await expect(h.api.triageQueue(ID_A, { repositoryId: "../x" })).rejects.toBeInstanceOf(ClientInputError);
    await expect(h.api.revokeSession("../../organizations")).rejects.toBeInstanceOf(ClientInputError);
    expect(h.api.organizationExportUrl(ID_A, "dependencies", "sarif", { scanner: ["trivy"] })).toBe(
      `/api/v1/organizations/${ID_A}/export?view=dependencies&format=sarif&scanner=trivy`,
    );
    expect(h.api.repositoryExportUrl(ID_A, "csv", { state: ["OPEN"] })).toBe(
      `/api/v1/repositories/${ID_A}/findings/export?format=csv&state=OPEN`,
    );
    expect(() => h.api.repositoryExportUrl("not-a-uuid", "csv")).toThrow(ClientInputError);
    expect(() => h.api.organizationExportUrl(ID_A, "triage", "csv", { scanner: ["BAD SCANNER"] })).toThrow(ClientInputError);
  });

  it("pagina escaneos con cursor y límite", async () => {
    const h = harness(() => json({ items: [], next_cursor: null }));
    await h.api.scans(ID_A, { limit: 50, cursor: "abc" });
    expect(h.calls[0]?.url).toBe(`/api/v1/repositories/${ID_A}/scans?limit=50&cursor=abc`);
  });

  it("filtra findings solo con los parámetros del contrato", async () => {
    const h = harness(() => json({ items: [], next_cursor: null }));
    await h.api.findings(ID_A, { severity: ["HIGH"], onlyNew: true, seenInScan: ID_B, limit: 20 });
    const query = new URL(h.calls[0]?.url ?? "", "http://x").searchParams;
    expect([...query.keys()].sort()).toEqual(["limit", "only_new", "seen_in_scan", "severity"]);
  });

  it("repite los parámetros multivalor en lugar de unirlos", async () => {
    const h = harness(() => json({ items: [], next_cursor: null }));
    await h.api.findings(ID_A, {
      severity: ["CRITICAL", "HIGH"],
      state: ["OPEN"],
      scanner: ["semgrep", "trivy"],
    });
    expect(h.calls[0]?.url).toBe(
      `/api/v1/repositories/${ID_A}/findings?severity=CRITICAL&severity=HIGH&state=OPEN&scanner=semgrep&scanner=trivy`,
    );
  });

  it.each([
    { scanner: ["Semgrep"] },
    { scanner: ["a&b=c"] },
    { scanner: ["semgrep\r\nX: y"] },
    { scanner: Array.from({ length: 17 }, () => "semgrep") },
    { seenInScan: "../x" },
  ])("rechaza el filtro %j antes de salir a la red", async (filter) => {
    const h = harness(() => json({}));
    await expect(h.api.findings(ID_A, filter)).rejects.toBeInstanceOf(ClientInputError);
    expect(h.calls).toEqual([]);
  });

  it("pagina logs por número de secuencia", async () => {
    const h = harness(() => json({ items: [] }));
    await h.api.scanLogs(ID_A, { afterSeq: 7, limit: 100 });
    expect(h.calls[0]?.url).toBe(`/api/v1/scans/${ID_A}/logs?after_seq=7&limit=100`);
  });

  it("inicia un escaneo con clave de idempotencia y distingue creado de repetido", async () => {
    const created = harness(() => json({ id: ID_A }, 202));
    expect((await created.api.startScan(ID_A, { ref: "main", commit: "a".repeat(40) }, KEY)).created).toBe(true);
    expect(headersOf(created.calls[0])["Idempotency-Key"]).toBe(KEY);
    const replay = harness(() => json({ id: ID_A }, 200));
    expect((await replay.api.startScan(ID_A, { ref: "main", commit: "a".repeat(40) }, KEY)).created).toBe(false);
  });

  it.each(["", "corta", "con espacios 0123456789", "x".repeat(129), "a\r\nb-0123456789abcdef"])(
    "rechaza la clave de idempotencia %j",
    async (key) => {
      const h = harness(() => json({}));
      await expect(
        h.api.startScan(ID_A, { ref: "main", commit: "a".repeat(40) }, key),
      ).rejects.toBeInstanceOf(ClientInputError);
      expect(h.calls).toEqual([]);
    },
  );

  it("registra triage y cancelación con POST", async () => {
    const h = harness(() => json({}, 201));
    await h.api.triage(ID_A, { state: "FALSE_POSITIVE", justification: "razón" } as never);
    await h.api.cancelScan(ID_B);
    expect(h.calls.map((c) => [c.init.method, c.url])).toEqual([
      ["POST", `/api/v1/findings/${ID_A}/triage`],
      ["POST", `/api/v1/scans/${ID_B}/cancel`],
    ]);
  });

  it("las vistas de organización piden su ruta fija y nada más", async () => {
    const h = harness(() => json({ items: [], next_cursor: null }));
    await h.api.organizationStats(ID_A);
    await h.api.organizationScans(ID_A);
    await h.api.acceptedRisks(ID_A);
    await h.api.ruleCatalog(ID_A);
    await h.api.dependencyFindings(ID_A);
    await h.api.policyHistory(ID_A);
    await h.api.auditLog(ID_A, { limit: 20, cursor: "41" });
    expect(h.calls.map((c) => c.url)).toEqual([
      `/api/v1/organizations/${ID_A}/stats`,
      `/api/v1/organizations/${ID_A}/scans`,
      `/api/v1/organizations/${ID_A}/accepted-risks`,
      `/api/v1/organizations/${ID_A}/rules`,
      `/api/v1/organizations/${ID_A}/dependencies`,
      `/api/v1/organizations/${ID_A}/policy`,
      `/api/v1/organizations/${ID_A}/audit-log?limit=20&cursor=41`,
    ]);
  });

  it.each(["", "abc", "-1", "1".repeat(13), "1&x=2"])("rechaza el cursor de auditoría %j sin llamar", async (cursor) => {
    const h = harness(() => json({ items: [], next_cursor: null }));
    await expect(h.api.auditLog(ID_A, { cursor })).rejects.toBeInstanceOf(ClientInputError);
    expect(h.calls).toEqual([]);
  });

  it("propaga la cancelación a las lecturas", async () => {
    const controller = new AbortController();
    const h = harness(() => json({}));
    await h.api.scan(ID_A, { signal: controller.signal });
    controller.abort();
    expect(h.calls[0]?.init.signal?.aborted).toBe(true);
  });
});
