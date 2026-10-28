import { flushPromises } from "@vue/test-utils";
import { afterEach, describe, expect, it } from "vitest";

import { formFrom, toRequest } from "@/features/edit-policy";

import { boot, jsonResponse, problemResponse, SESSION } from "./harness";
import type { Responder } from "./harness";

const ORG = "11111111-1111-4111-8111-111111111111";
const OTHER = "66666666-6666-4666-8666-666666666666";
const BOB = "77777777-7777-4777-8777-777777777777";

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

const counts = { critical: 0, high: 0, medium: 0, low: 0, info: 0 };
const bob = {
  id: BOB,
  display_name: "<b>Bob</b>",
  email: "bob@example.test",
  last_login_at: "2026-10-12T10:00:00Z",
  live_sessions: 2,
  platform_admin: false,
  memberships: [{ organization_id: ORG, login: "acme", role: "VIEWER" }],
};

function server(platformAdmin: boolean, role: "VIEWER" | "ADMIN" = "ADMIN", extra: Responder = () => problemResponse(404, "x")): Responder {
  return (url, init) => {
    if (url === "/api/v1/session") return jsonResponse(SESSION);
    if (url === "/api/v1/me") return jsonResponse({ user_id: SESSION.user.id, display_name: "Alicia", platform_admin: platformAdmin });
    if (url === "/api/v1/organizations") {
      return jsonResponse({ items: [{ id: ORG, login: "acme", role, status: "ACTIVE", open_findings: counts }] });
    }
    return extra(url, init);
  };
}

describe("sección de plataforma", () => {
  it("solo aparece a un administrador de plataforma", async () => {
    const plain = await start(server(false), "/");
    expect(plain.text()).not.toContain("Plataforma");
    const admin = await start(server(true), "/");
    const links = [...admin.host.querySelectorAll(".app-sidebar__link")].map((a) => a.textContent.trim());
    expect(links).toEqual(expect.arrayContaining(["Usuarios", "Salud", "Webhooks"]));
  });
});

describe("usuarios", () => {
  const respond: Responder = (url, init) => {
    if (url === "/api/v1/platform/users") return jsonResponse({ items: [bob] });
    if (url === "/api/v1/platform/organizations") {
      return jsonResponse({
        items: [
          { id: ORG, login: "acme", status: "ACTIVE", members: 1, admins: 1 },
          { id: OTHER, login: "globex", status: "ACTIVE", members: 0, admins: 0 },
        ],
      });
    }
    if (url.startsWith(`/api/v1/platform/users/${BOB}/`) && init.method === "PUT") return new Response(null, { status: 204 });
    if (url === `/api/v1/platform/users/${BOB}/sessions`) return jsonResponse({ revoked: 2 });
    return problemResponse(404, "x");
  };

  it("pinta los nombres como texto, cambia roles, añade organizaciones y cierra sesiones", async () => {
    const app = await start(server(true, "ADMIN", respond), "/platform/users");
    expect(app.host.querySelector("main b")).toBeNull();
    expect(app.text()).toContain("<b>Bob</b>");
    app.button("Gestionar")?.click();
    await flushPromises();

    const role = app.host.querySelector<HTMLSelectElement>("dialog select[aria-label^='Rol en']");
    if (role) {
      role.value = "TRIAGER";
      role.dispatchEvent(new Event("change"));
    }
    await flushPromises();
    const put = app.calls.find((c) => c.init.method === "PUT");
    expect(put?.url).toBe(`/api/v1/platform/users/${BOB}/memberships/${ORG}`);
    expect(put?.init.body).toBe('{"role":"TRIAGER"}');

    const [orgSelect] = [...app.host.querySelectorAll<HTMLSelectElement>(".users__add select")];
    if (orgSelect) {
      orgSelect.value = OTHER;
      orgSelect.dispatchEvent(new Event("change"));
    }
    await flushPromises();
    app.host.querySelector<HTMLFormElement>(".users__add")?.requestSubmit();
    await flushPromises();
    expect(app.calls.some((c) => c.init.method === "PUT" && c.url.endsWith(`/memberships/${OTHER}`))).toBe(true);

    app.button("Cerrar todas sus sesiones")?.click();
    await flushPromises();
    app.button("Confirmar cierre")?.click();
    await flushPromises();
    expect(app.calls.some((c) => c.init.method === "DELETE" && c.url === `/api/v1/platform/users/${BOB}/sessions`)).toBe(true);
    expect(app.text()).toContain("Cerradas 2 sesiones.");
  });
});

describe("salud y webhooks", () => {
  it("avisa de un latido viejo y nombra cada componente", async () => {
    const report = {
      stale: true,
      heartbeat_at: "2026-10-12T09:00:00Z",
      components: [
        { component: "database", status: "OK", detail: "Responde", latency_ms: 3, checked_at: "2026-10-12T10:00:00Z" },
        { component: "scan-runner", status: "DOWN", detail: "Sin latido", latency_ms: null, checked_at: "2026-10-12T09:00:00Z" },
      ],
    };
    const app = await start(
      server(true, "ADMIN", (url) => (url === "/api/v1/platform/health" ? jsonResponse(report) : problemResponse(404, "x"))),
      "/platform/health",
    );
    expect(app.text()).toContain("Base de datos");
    expect(app.text()).toContain("Caído");
    expect(app.text()).toContain("el worker o el scheduler no están funcionando");
  });

  it("suma rechazos por motivo y por hora", async () => {
    const activity = {
      recent: [{ delivery_id: "0a1b2c3d-0000-4000-8000-000000000001", event: "push", outcome: "ENQUEUED", received_at: "2026-10-12T10:00:00Z" }],
      outcomes: [{ outcome: "ENQUEUED", count: 1 }],
      counters: [
        { minute: "2026-10-12T10:01:00Z", reason: "SIGNATURE", count: 3 },
        { minute: "2026-10-12T10:59:00Z", reason: "SIGNATURE", count: 2 },
        { minute: "2026-10-12T11:00:00Z", reason: "DUPLICATE", count: 1 },
      ],
    };
    const app = await start(
      server(true, "ADMIN", (url) => (url === "/api/v1/platform/webhooks" ? jsonResponse(activity) : problemResponse(404, "x"))),
      "/platform/webhooks",
    );
    const rows = [...app.host.querySelectorAll("tbody tr")].map((r) => [...r.querySelectorAll("td")].map((c) => c.textContent.trim()));
    expect(rows[0]?.[0]).toBe("2026-10-12 11:00");
    expect(rows[1]).toEqual(["2026-10-12 10:00", "5", "0", "0", "0", "5"]);
    expect(app.text()).toContain("Escaneo encolado");
  });
});

describe("editar la política", () => {
  const active = {
    version: 3,
    active: true,
    created_at: "2026-10-12T10:00:00Z",
    gates: [{ id: "max-critical", scope: "ALL_OPEN", max: [{ severity: "CRITICAL", value: 0 }] }],
    sla_days: [{ severity: "HIGH", value: 14 }],
    fail_on_sla_breach: false,
  };

  it("valida en el cliente y publica una versión nueva", async () => {
    const app = await start(
      server(false, "ADMIN", (url, init) => {
        if (url === `/api/v1/organizations/${ORG}/policy` && init.method === "PUT") return jsonResponse({ version: 4 }, 201);
        if (url === `/api/v1/organizations/${ORG}/policy`) return jsonResponse({ items: [active] });
        return problemResponse(404, "x");
      }),
      `/organizations/${ORG}/policy`,
    );
    app.button("Editar política")?.click();
    await flushPromises();
    const id = app.host.querySelector<HTMLInputElement>(".policy-editor__gate input");
    if (id) {
      id.value = "Mal ID";
      id.dispatchEvent(new Event("input"));
    }
    app.host.querySelector<HTMLFormElement>(".policy-editor")?.requestSubmit();
    await flushPromises();
    expect(app.text()).toContain("el identificador solo admite minúsculas");
    expect(app.calls.some((c) => c.init.method === "PUT")).toBe(false);

    if (id) {
      id.value = "max-critical";
      id.dispatchEvent(new Event("input"));
    }
    app.host.querySelector<HTMLFormElement>(".policy-editor")?.requestSubmit();
    await flushPromises();
    const put = app.calls.find((c) => c.init.method === "PUT");
    expect(JSON.parse(put?.init.body as string)).toEqual({
      gates: [{ id: "max-critical", scope: "ALL_OPEN", max: [{ severity: "CRITICAL", value: 0 }] }],
      sla_days: [{ severity: "HIGH", value: 14 }],
      fail_on_sla_breach: false,
    });
    expect(app.text()).toContain("Publicada la versión 4.");
  });

  it("un VIEWER no ve el botón de edición", async () => {
    const app = await start(
      server(false, "VIEWER", (url) => (url === `/api/v1/organizations/${ORG}/policy` ? jsonResponse({ items: [active] }) : problemResponse(404, "x"))),
      `/organizations/${ORG}/policy`,
    );
    expect(app.button("Editar política")).toBeUndefined();
  });

  it("rechaza números fuera de rango, puertas sin límites e identificadores repetidos", () => {
    const form = formFrom(undefined);
    form.gates.push({ id: "a", scope: "NEW_FINDINGS", max: { CRITICAL: "", HIGH: "", MEDIUM: "", LOW: "", INFO: "" } });
    form.gates.push({ id: "a", scope: "NEW_FINDINGS", max: { CRITICAL: "-1", HIGH: "1.5", MEDIUM: "", LOW: "", INFO: "" } });
    form.sla.HIGH = "0";
    const { request, problems } = toRequest(form);
    expect(request).toBeNull();
    expect(problems.join(" ")).toContain("al menos una severidad");
    expect(problems.join(" ")).toContain("está repetido");
    expect(problems.join(" ")).toContain("entre 1 y 3650");
  });
});
