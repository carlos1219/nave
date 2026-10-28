import { flushPromises } from "@vue/test-utils";
import { afterEach, describe, expect, it } from "vitest";

import { boot, jsonResponse, problemResponse, SESSION } from "./harness";
import type { Responder } from "./harness";

const ORG = "11111111-1111-4111-8111-111111111111";
const REPO = "22222222-2222-4222-8222-222222222222";
const SCAN = "33333333-3333-4333-8333-333333333333";
const NEW_SCAN = "55555555-5555-4555-8555-555555555555";
const FINDING = "44444444-4444-4444-8444-444444444444";
const DIGEST = `sha256:${"a".repeat(64)}`;
const SESSION_ID = "0123456789abcdef0123456789abcdef";

type Booted = Awaited<ReturnType<typeof boot>>;
const open: Booted[] = [];
async function start(respond: Responder, path: string): Promise<Booted> {
  const app = await boot(respond, path);
  open.push(app);
  return app;
}
afterEach(() => {
  for (const app of open.splice(0)) app.unmount();
});

const counts = { critical: 1, high: 0, medium: 0, low: 0, info: 0 };
const tool = { scanner: "semgrep", version: "1.180.0", image_digest: DIGEST, findings: 2 };
const scan = (overrides: Record<string, unknown> = {}) => ({
  id: SCAN, repository_id: REPO, ref: "feature/x", commit: "a".repeat(40), status: "COMPLETED", trigger: "WEBHOOK",
  queued_at: "2026-10-12T10:00:00Z", started_at: "2026-10-12T10:00:01Z", finished_at: "2026-10-12T10:02:00Z",
  pull_request_number: 12, status_reason: null, superseded_by: null,
  summary: { new: counts, open: counts, truncated: false, verdict: "FAIL", tools: [tool] },
  ...overrides,
});
const repository = { id: REPO, owner: "acme", name: "<i>web</i>" };
const finding = {
  id: FINDING, repository_id: REPO, severity: "CRITICAL", title: "<img src=x onerror=alert(1)>", scanner: "semgrep",
  rule_id: "r.sqli", category: "SAST", cwe: [], cve: [], first_seen_at: "2026-10-01T10:00:00Z",
  last_seen_at: "2026-10-12T10:00:00Z", first_seen_scan_id: SCAN, last_seen_scan_id: SCAN, triage_state: "OPEN",
  triage_expires_at: null,
  occurrence: { is_new: true, message: "m", snippet: null, scan_id: SCAN, location: { path: "a.py", start_line: 3, end_line: 3 } },
};

function server(role: "VIEWER" | "TRIAGER" | "ADMIN", extra: Responder = () => problemResponse(404, "x")): Responder {
  return (url, init) => {
    if (url === "/api/v1/session") return jsonResponse(SESSION);
    if (url === "/api/v1/organizations") {
      return jsonResponse({ items: [{ id: ORG, login: "acme", role, status: "ACTIVE", open_findings: counts }] });
    }
    if (url === `/api/v1/organizations/${ORG}/repositories`) {
      return jsonResponse({ items: [{ ...repository, default_branch: "main", enabled: true, open_findings: counts }] });
    }
    return extra(url, init);
  };
}

describe("barra lateral", () => {
  it("agrupa las secciones, marca la actual y se contrae sin perder los nombres accesibles", async () => {
    const app = await start(server("VIEWER"), `/organizations/${ORG}/triage`);
    const headings = [...app.host.querySelectorAll(".app-sidebar h2")].map((h) => h.textContent.trim());
    expect(headings).toEqual(["Panorama", "Triage", "Escaneos", "Gobierno"]);
    const current = app.host.querySelector(".app-sidebar [aria-current='page']");
    expect(current?.textContent.trim()).toBe("Cola de triage");
    expect([...app.host.querySelectorAll(".app-sidebar svg")].every((s) => s.getAttribute("aria-hidden") === "true")).toBe(true);

    const toggle = app.host.querySelector<HTMLButtonElement>(".app-sidebar__toggle");
    expect(toggle?.getAttribute("aria-expanded")).toBe("true");
    toggle?.click();
    await flushPromises();
    expect(toggle?.getAttribute("aria-expanded")).toBe("false");
    const link = app.host.querySelector(".app-sidebar [aria-current='page']");
    expect(link?.getAttribute("title")).toBe("Cola de triage");
    expect(link?.querySelector(".visually-hidden")?.textContent).toBe("Cola de triage");
    toggle?.click();
    await flushPromises();
  });

  it("dentro de una PR marca «Pull requests» como sección actual", async () => {
    const app = await start(
      server("VIEWER", (url) => (url === `/api/v1/scans/${SCAN}` ? jsonResponse(scan()) : jsonResponse({ items: [], next_cursor: null }))),
      `/organizations/${ORG}/pull-requests/${SCAN}`,
    );
    expect(app.host.querySelector(".app-sidebar [aria-current='page']")?.textContent.trim()).toBe("Pull requests");
  });
});

describe("cola de triage", () => {
  const queue: Responder = (url) =>
    url.startsWith(`/api/v1/organizations/${ORG}/triage-queue`)
      ? jsonResponse({ items: [{ finding, repository, justification: null }], next_cursor: null })
      : problemResponse(404, "x");

  it("pinta el texto del hallazgo como texto y filtra por severidad en el servidor", async () => {
    const app = await start(server("VIEWER", queue), `/organizations/${ORG}/triage`);
    expect(app.host.querySelector("main img")).toBeNull();
    expect(app.text()).toContain("<img src=x onerror=alert(1)>");
    expect(app.text()).toContain("<i>web</i>");
    const critical = [...app.host.querySelectorAll<HTMLInputElement>(".queue-filter__box")][0];
    critical?.click();
    await flushPromises();
    const last = app.calls.filter((c) => c.url.includes("/triage-queue")).at(-1);
    expect(last?.url).toContain("severity=CRITICAL");
  });

  it("ofrece la descarga en CSV y SARIF con el filtro aplicado", async () => {
    const app = await start(server("VIEWER", queue), `/organizations/${ORG}/triage`);
    [...app.host.querySelectorAll<HTMLInputElement>(".queue-filter__box")][1]?.click();
    await flushPromises();
    const hrefs = [...app.host.querySelectorAll<HTMLAnchorElement>(".export-links__link")].map((a) => a.getAttribute("href"));
    expect(hrefs).toEqual([
      `/api/v1/organizations/${ORG}/export?view=triage&format=csv&severity=HIGH`,
      `/api/v1/organizations/${ORG}/export?view=triage&format=sarif&severity=HIGH`,
    ]);
    expect([...app.host.querySelectorAll(".export-links__link")].every((a) => a.hasAttribute("download"))).toBe(true);
  });
});

describe("pull requests", () => {
  it("lista cada PR y su detalle pide solo los hallazgos nuevos de ese escaneo", async () => {
    const respond: Responder = (url) => {
      if (url === `/api/v1/organizations/${ORG}/pull-requests`) return jsonResponse({ items: [{ scan: scan(), repository }] });
      if (url === `/api/v1/scans/${SCAN}`) return jsonResponse(scan());
      if (url.startsWith(`/api/v1/repositories/${REPO}/findings`)) return jsonResponse({ items: [finding], next_cursor: null });
      return problemResponse(404, "x");
    };
    const list = await start(server("VIEWER", respond), `/organizations/${ORG}/pull-requests`);
    expect(list.text()).toContain("#12");
    expect(list.text()).toContain("CRITICAL 1");
    const detail = await start(server("VIEWER", respond), `/organizations/${ORG}/pull-requests/${SCAN}`);
    await flushPromises();
    const call = detail.calls.find((c) => c.url.includes("/findings"));
    expect(call?.url).toContain(`seen_in_scan=${SCAN}`);
    expect(call?.url).toContain("only_new=true");
    expect(detail.text()).toContain("PR #12");
    expect(detail.text()).toContain("Nuevo");
  });
});

describe("escaneos fallidos", () => {
  const failed = scan({ status: "FAILED", summary: null, status_reason: { code: "SCANNER_FAILED", detail: "exit 2" } });
  const respond: Responder = (url, init) => {
    if (url === `/api/v1/organizations/${ORG}/failed-scans`) return jsonResponse({ items: [{ scan: failed, repository, recovered: false }] });
    if (url === `/api/v1/repositories/${REPO}/scans` && init.method === "POST") {
      return jsonResponse({ scan_id: NEW_SCAN, created: true }, 202);
    }
    if (url === `/api/v1/scans/${NEW_SCAN}`) return jsonResponse(scan({ id: NEW_SCAN, status: "QUEUED", summary: null }));
    return jsonResponse({ items: [], next_cursor: null });
  };

  it("explica la causa y solo un TRIAGER puede relanzar", async () => {
    const viewer = await start(server("VIEWER", respond), `/organizations/${ORG}/failed-scans`);
    expect(viewer.text()).toContain("El escáner terminó con error");
    expect(viewer.text()).toContain("Sin resolver");
    expect(viewer.button("Relanzar")).toBeUndefined();

    const triager = await start(server("TRIAGER", respond), `/organizations/${ORG}/failed-scans`);
    triager.button("Relanzar")?.click();
    await flushPromises();
    const post = triager.calls.find((c) => c.init.method === "POST");
    expect(JSON.parse(post?.init.body as string)).toEqual({ ref: "feature/x", commit: "a".repeat(40) });
    expect(triager.path()).toBe(`/scans/${NEW_SCAN}`);
  });
});

describe("escáneres", () => {
  it("muestra las versiones con su uso y avisa de lo que no se registra", async () => {
    const report = {
      versions: [{ ...tool, scans: 3, first_used_at: "2026-10-01T10:00:00Z", last_used_at: "2026-10-12T10:00:00Z" }],
      recent: [{ scan: scan(), repository }],
      without_tools: 2,
    };
    const app = await start(
      server("VIEWER", (url) => (url === `/api/v1/organizations/${ORG}/scanners` ? jsonResponse(report) : problemResponse(404, "x"))),
      `/organizations/${ORG}/scanners`,
    );
    expect(app.text()).toContain("1.180.0");
    expect(app.text()).toContain("Vigente");
    expect(app.text()).toContain("2 escaneos de la muestra");
    expect(app.text()).toContain("base de datos de vulnerabilidades de Trivy");
  });
});

describe("mis sesiones", () => {
  it("marca la actual y cierra otra con DELETE", async () => {
    const sessions = [
      { id: "f".repeat(32), created_at: "2026-10-12T08:00:00Z", last_seen_at: "2026-10-12T09:00:00Z", expires_at: "2026-10-12T20:00:00Z", current: true },
      { id: SESSION_ID, created_at: "2026-10-11T08:00:00Z", last_seen_at: "2026-10-11T09:00:00Z", expires_at: "2026-10-11T20:00:00Z", current: false },
    ];
    const app = await start(
      server("VIEWER", (url, init) => {
        if (url === `/api/v1/me/sessions/${SESSION_ID}` && init.method === "DELETE") return new Response(null, { status: 204 });
        if (url === "/api/v1/me/sessions") return jsonResponse({ items: sessions });
        return problemResponse(404, "x");
      }),
      "/sessions",
    );
    expect(app.text()).toContain("Esta sesión");
    app.button("Cerrar")?.click();
    await flushPromises();
    const deleted = app.calls.find((c) => c.init.method === "DELETE");
    expect(deleted?.url).toBe(`/api/v1/me/sessions/${SESSION_ID}`);
    expect((deleted?.init.headers as Record<string, string>)["X-CSRF-Token"]).toBe(SESSION.csrf_token);
  });
});
