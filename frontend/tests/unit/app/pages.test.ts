import { flushPromises } from "@vue/test-utils";
import { afterEach, describe, expect, it, vi } from "vitest";

import { boot, jsonResponse, problemResponse, SESSION } from "./harness";
import type { Responder } from "./harness";

const ORG = "11111111-1111-4111-8111-111111111111";
const REPO = "22222222-2222-4222-8222-222222222222";
const SCAN = "33333333-3333-4333-8333-333333333333";
const FINDING = "44444444-4444-4444-8444-444444444444";

type Booted = Awaited<ReturnType<typeof boot>>;
const open: Booted[] = [];
async function start(respond: Responder, path: string): Promise<Booted> {
  const app = await boot(respond, path);
  open.push(app);
  return app;
}
afterEach(() => {
  for (const app of open.splice(0)) app.unmount();
  vi.useRealTimers();
});

const counts = { critical: 1, high: 0, medium: 0, low: 0, info: 0 };
const scanBody = (status: string) => ({
  id: SCAN, repository_id: REPO, ref: "main", commit: "a".repeat(40), status, trigger: "API",
  queued_at: "2026-10-12T10:00:00Z", started_at: null, finished_at: null, pull_request_number: null,
  status_reason: null, superseded_by: null,
  summary: status === "COMPLETED" ? { new: counts, open: counts, truncated: false, verdict: "FAIL" } : null,
});
const finding = (title: string) => ({
  id: FINDING, repository_id: REPO, severity: "HIGH", title, scanner: "semgrep", rule_id: "r", category: "c",
  cwe: [], cve: [], first_seen_at: "2026-10-12T10:00:00Z", last_seen_at: "2026-10-12T10:00:00Z",
  first_seen_scan_id: SCAN, last_seen_scan_id: SCAN, triage_state: "OPEN", triage_expires_at: null,
  occurrence: { is_new: true, message: "m", snippet: null, scan_id: SCAN, location: { path: "a.py", start_line: 3, end_line: 3 } },
});

function server(role: "VIEWER" | "TRIAGER" | "ADMIN", extra: Responder = () => problemResponse(404, "not-found")): Responder {
  return (url, init) => {
    if (url === "/api/v1/session") return jsonResponse(SESSION);
    if (url === "/api/v1/organizations") {
      return jsonResponse({ items: [{ id: ORG, login: "<b>acme</b>", role, status: "ACTIVE", open_findings: { ...counts, high: 3 } }] });
    }
    if (url === `/api/v1/organizations/${ORG}/repositories`) {
      return jsonResponse({ items: [{ id: REPO, owner: "acme", name: "web", default_branch: "main", enabled: true, open_findings: counts }] });
    }
    if (url === `/api/v1/organizations/${ORG}/members`) return jsonResponse({ items: [] });
    return extra(url, init);
  };
}

describe("rutas cerradas", () => {
  it("un identificador que no es UUID cae en not-found", async () => {
    const app = await start(server("ADMIN"), "/scans/no-es-uuid");
    expect(app.router.currentRoute.value.name).toBe("not-found");
    expect(app.calls.every((c) => !c.url.includes("no-es-uuid"))).toBe(true);
  });
});

describe("inicio y organización", () => {
  it("muestra el nombre de la organización como texto", async () => {
    const app = await start(server("VIEWER"), "/");
    expect(app.host.querySelector("b")).toBeNull();
    expect(app.text()).toContain("<b>acme</b>");
  });

  it("muestra las vulnerabilidades abiertas por severidad con su etiqueta y marca la fila", async () => {
    const app = await start(server("VIEWER"), "/");
    const items = [...app.host.querySelectorAll(".open-findings__item")].map((n) => n.textContent.replace(/\s+/g, " ").trim());
    expect(items).toEqual(["CRITICAL 1", "HIGH 3"]);
    expect(app.host.querySelector("tbody tr")?.getAttribute("data-tone")).toBe("critical");
  });

  it("los miembros solo se piden y muestran a un ADMIN", async () => {
    const viewer = await start(server("VIEWER"), `/organizations/${ORG}`);
    expect(viewer.calls.some((c) => c.url.endsWith("/members"))).toBe(false);
    const admin = await start(server("ADMIN"), `/organizations/${ORG}`);
    expect(admin.calls.some((c) => c.url.endsWith("/members"))).toBe(true);
  });
});

describe("vistas de organización", () => {
  const stats = {
    open: counts,
    by_scanner: [{ scanner: "semgrep", open: counts }],
    by_repository: [{ repository: { id: REPO, owner: "acme", name: "<i>web</i>" }, open: counts }],
    top_rules: [
      { rule_id: "<b>rule</b>", scanner: "semgrep", category: "SAST", max_severity: "CRITICAL", open: 1, total: 2, repositories: 1, last_seen_at: "2026-10-12T10:00:00Z" },
    ],
    fix_times: [],
    sla: [],
    trend: [
      { day: "2026-10-11", open: counts },
      { day: "2026-10-12", open: counts },
    ],
  };
  const insights: Responder = (url) => (url === `/api/v1/organizations/${ORG}/stats` ? jsonResponse(stats) : problemResponse(404, "x"));

  it("la sección de auditoría solo aparece a un ADMIN", async () => {
    const viewer = await start(server("VIEWER", insights), `/organizations/${ORG}/stats`);
    const tabs = (app: Booted) => [...app.host.querySelectorAll(".app-sidebar__link")].map((t) => t.textContent.trim());
    expect(tabs(viewer)).not.toContain("Auditoría");
    expect(tabs(viewer)).toContain("Estadísticas");
    const admin = await start(server("ADMIN", insights), `/organizations/${ORG}/stats`);
    expect(tabs(admin)).toContain("Auditoría");
  });

  it("pinta las estadísticas con gráficas SVG y el texto del servidor como texto", async () => {
    const app = await start(server("VIEWER", insights), `/organizations/${ORG}/stats`);
    await flushPromises();
    expect(app.host.querySelectorAll("svg[role='img']").length).toBeGreaterThan(2);
    expect(app.host.querySelector("i, main b")).toBeNull();
    expect(app.text()).toContain("<i>web</i>");
    expect(app.text()).toContain("<b>rule</b>");
    expect(app.text()).toContain("La política de la organización no define plazos.");
    expect(app.host.innerHTML).not.toContain("style=");
  });
});

describe("repositorio", () => {
  const scans: Responder = (url) =>
    url.startsWith(`/api/v1/repositories/${REPO}/scans`) ? jsonResponse({ items: [scanBody("COMPLETED")], next_cursor: null }) : problemResponse(404, "x");

  it("el formulario de inicio de escaneo solo aparece a TRIAGER o más", async () => {
    const viewer = await start(server("VIEWER", scans), `/organizations/${ORG}/repositories/${REPO}`);
    expect(viewer.host.querySelector("form[aria-label='Iniciar escaneo']")).toBeNull();
    const triager = await start(server("TRIAGER", scans), `/organizations/${ORG}/repositories/${REPO}`);
    expect(triager.host.querySelector("form[aria-label='Iniciar escaneo']")).not.toBeNull();
    expect(triager.text()).toContain("Completado");
  });
});

describe("escaneo", () => {
  it("muestra resumen, quality gate y logs con caracteres de control visibles", async () => {
    const app = await start(
      server("VIEWER", (url) => {
        if (url === `/api/v1/scans/${SCAN}`) return jsonResponse(scanBody("COMPLETED"));
        if (url === `/api/v1/scans/${SCAN}/quality-gate`) {
          return jsonResponse({ evaluated: true, verdict: "FAIL", reasons: [] });
        }
        if (url.startsWith(`/api/v1/scans/${SCAN}/logs`)) {
          return jsonResponse({ items: [{ seq: 1, at: "2026-10-12T10:00:01Z", level: "ERROR", message: "x‮y\u001b[31mred" }], next_cursor: null });
        }
        return problemResponse(404, "x");
      }),
      `/scans/${SCAN}`,
    );
    await flushPromises();
    expect(app.text()).toContain("Quality gate:");
    expect(app.text()).toContain("FAIL");
    expect(app.text()).not.toContain("‮");
    expect(app.text()).not.toContain("\u001b");
    expect(app.button("Cancelar escaneo")).toBeUndefined();
  });
});

describe("findings", () => {
  it("descarta filtros hostiles de la URL y no interpreta títulos como HTML", async () => {
    const app = await start(
      server("VIEWER", (url) =>
        url.startsWith(`/api/v1/repositories/${REPO}/findings`)
          ? jsonResponse({ items: [finding("<img src=x onerror=alert(1)>")], next_cursor: null })
          : problemResponse(404, "x"),
      ),
      `/repositories/${REPO}/findings?severity=HIGH&severity=%3Cscript%3E&state=bad&seen_in_scan=nope&x=1`,
    );
    await flushPromises();
    const request = app.calls.find((c) => c.url.includes("/findings"));
    expect(request?.url).toContain("severity=HIGH");
    expect(request?.url).not.toContain("script");
    expect(request?.url).not.toContain("bad");
    expect(request?.url).not.toContain("nope");
    expect(app.host.querySelector("img")).toBeNull();
    expect(app.text()).toContain("<img src=x");
    expect(app.path()).not.toContain("script");
    expect(app.path()).not.toContain("x=1");
  });
});

describe("finding y triage", () => {
  it("envía la decisión con el token CSRF en memoria", async () => {
    const app = await start(
      server("TRIAGER", (url, init) => {
        if (url === `/api/v1/findings/${FINDING}` && init.method !== "POST") {
          return jsonResponse({ ...finding("t"), decisions: [] });
        }
        if (url === `/api/v1/findings/${FINDING}/triage`) return jsonResponse({ decision_id: SCAN, state: "FALSE_POSITIVE" }, 201);
        return problemResponse(404, "x");
      }),
      `/findings/${FINDING}`,
    );
    await flushPromises();
    const text = app.host.querySelector("textarea");
    expect(text).not.toBeNull();
    if (text === null) return;
    text.value = "no es explotable en este contexto";
    text.dispatchEvent(new Event("input"));
    app.host.querySelector("form[aria-label='Triage del finding']")?.dispatchEvent(new Event("submit", { cancelable: true }));
    await flushPromises();
    const post = app.calls.find((c) => c.url.endsWith("/triage"));
    expect(post).toBeDefined();
    expect(new Headers(post?.init.headers).get("X-CSRF-Token")).toBe(SESSION.csrf_token);
  });
});
