import { flushPromises } from "@vue/test-utils";
import { afterEach, describe, expect, it } from "vitest";

import { boot, jsonResponse, problemResponse, SESSION } from "./harness";

type Booted = Awaited<ReturnType<typeof boot>>;
const open: Booted[] = [];
async function start(...args: Parameters<typeof boot>): Promise<Booted> {
  const app = await boot(...args);
  open.push(app);
  return app;
}
afterEach(() => {
  for (const app of open.splice(0)) app.unmount();
});

const signedIn = (url: string): Response =>
  url === "/api/v1/session" ? jsonResponse(SESSION) : problemResponse(404, "not-found");
const anonymous = (): Response => problemResponse(401, "unauthorized");

describe("arranque", () => {
  it("sin sesión lleva a la entrada y solo pregunta por la sesión", async () => {
    const app = await start(anonymous);
    expect(app.path()).toBe("/login");
    expect(app.calls.map((c) => c.url)).toEqual(["/api/v1/session"]);
    const link = app.host.querySelector<HTMLAnchorElement>("a.login-page__action");
    expect(link?.getAttribute("href")).toBe("/api/v1/auth/login");
  });

  it("con sesión muestra el inicio y el nombre", async () => {
    const app = await start(signedIn);
    expect(app.path()).toBe("/");
    expect(app.text()).toContain("Alicia");
    expect(app.button("Salir")).toBeDefined();
  });

  it("pregunta por la sesión una sola vez aunque haya varias navegaciones", async () => {
    const app = await start(signedIn);
    await Promise.all([app.router.push("/"), app.router.push("/login"), app.router.push("/")]);
    expect(app.calls.filter((c) => c.url === "/api/v1/session")).toHaveLength(1);
  });

  it("con sesión, la entrada redirige al inicio", async () => {
    const app = await start(signedIn, "/login");
    expect(app.path()).toBe("/");
  });
});

describe("redirección abierta (AM-706)", () => {
  it.each([
    "/login?next=https://evil.example",
    "/login?redirect=//evil.example/x",
    "/login?return_to=javascript:alert(1)",
  ])("ignora los parámetros de %s", async (path) => {
    const app = await start(anonymous, path);
    const link = app.host.querySelector<HTMLAnchorElement>("a.login-page__action");
    expect(link?.getAttribute("href")).toBe("/api/v1/auth/login");
    expect(app.calls.every((c) => !c.url.includes("evil"))).toBe(true);
  });

  it("no añade a la entrada ni la ruta que se intentó abrir", async () => {
    const app = await start(anonymous, "/");
    expect(app.router.currentRoute.value.fullPath).toBe("/login");
    expect(app.router.currentRoute.value.query).toEqual({});
  });
});

describe("rutas desconocidas", () => {
  it("muestran 'no encontrado' sin sesión y con sesión", async () => {
    expect((await start(anonymous, "/no/existe")).text()).toContain("No encontrado");
    expect((await start(signedIn, "/no/existe")).text()).toContain("No encontrado");
  });
});

describe("fallo al comprobar la sesión", () => {
  it("ofrece reintentar y se recupera", async () => {
    let up = false;
    const app = await start(() => (up ? jsonResponse(SESSION) : problemResponse(503, "unavailable")));
    expect(app.session.status).toBe("error");
    expect(app.text()).toContain("No se pudo comprobar la sesión");
    up = true;
    app.button("Reintentar")?.click();
    await flushPromises();
    expect(app.session.status).toBe("authenticated");
    expect(app.text()).toContain("Alicia");
  });

  it("un fallo de red no se confunde con una sesión caducada", async () => {
    const app = await start(() => {
      throw new TypeError("Failed to fetch");
    });
    expect(app.session.status).toBe("error");
    expect(app.path()).not.toBe("/login");
  });
});

describe("caducidad con la aplicación abierta", () => {
  it("un 401 posterior devuelve a la entrada y borra la sesión", async () => {
    const app = await start(signedIn);
    expect(app.session.csrfToken()).toBe(SESSION.csrf_token);
    app.session.expire();
    await flushPromises();
    expect(app.path()).toBe("/login");
    expect(app.session.csrfToken()).toBeUndefined();
    expect(app.text()).not.toContain("Alicia");
  });
});

describe("cierre de sesión", () => {
  it("envía POST con el token CSRF y vuelve a la entrada", async () => {
    const app = await start((url, init) =>
      url === "/api/v1/auth/logout" && init.method === "POST"
        ? new Response(null, { status: 204 })
        : signedIn(url),
    );
    app.button("Salir")?.click();
    await flushPromises();
    const logout = app.calls.find((c) => c.url === "/api/v1/auth/logout");
    expect((logout?.init.headers as Record<string, string>)["X-CSRF-Token"]).toBe(SESSION.csrf_token);
    expect(app.path()).toBe("/login");
    expect(app.session.csrfToken()).toBeUndefined();
  });

  it("si el servidor falla, la sesión sigue y se avisa", async () => {
    const app = await start((url) =>
      url === "/api/v1/auth/logout" ? problemResponse(503, "unavailable") : signedIn(url),
    );
    app.button("Salir")?.click();
    await flushPromises();
    expect(app.session.authenticated).toBe(true);
    expect(app.text()).toContain("No se pudo cerrar la sesión");
  });

  it("si el servidor ya no la reconoce (401), se da por cerrada", async () => {
    const app = await start((url) =>
      url === "/api/v1/auth/logout" ? problemResponse(401, "unauthorized") : signedIn(url),
    );
    app.button("Salir")?.click();
    await flushPromises();
    expect(app.path()).toBe("/login");
  });
});

describe("secretos y texto no confiable", () => {
  it("el token CSRF no está en almacenamiento, en el DOM ni en el estado de Pinia", async () => {
    const app = await start(signedIn);
    expect(localStorage.length).toBe(0);
    expect(sessionStorage.length).toBe(0);
    expect(document.cookie).toBe("");
    expect(app.host.innerHTML).not.toContain(SESSION.csrf_token);
    expect(JSON.stringify(app.pinia.state.value)).not.toContain(SESSION.csrf_token);
  });

  it("el nombre del IdP se muestra como texto, nunca como HTML", async () => {
    const hostile = '<img src=x onerror="alert(1)"><script>alert(2)</script>';
    const app = await start((url) =>
      url === "/api/v1/session"
        ? jsonResponse({ ...SESSION, user: { ...SESSION.user, display_name: hostile } })
        : problemResponse(404, "not-found"),
    );
    expect(app.host.querySelector("img")).toBeNull();
    expect(app.host.querySelector("script")).toBeNull();
    expect(app.text()).toContain("<img src=x");
  });

  it("hace visibles bidi, ancho cero y ANSI en el nombre del IdP (F7-21)", async () => {
    const esc = String.fromCharCode(0x1b);
    const hostile = `Ana‮fdp.exe​${esc}[31mroja${esc}[0m`;
    const app = await start((url) =>
      url === "/api/v1/session"
        ? jsonResponse({ ...SESSION, user: { ...SESSION.user, display_name: hostile } })
        : problemResponse(404, "not-found"),
    );
    expect(app.session.displayName).toBe("Ana<U+202E>fdp.exe<U+200B>roja");
    expect(app.text()).toContain("<U+202E>");
    expect(app.text()).not.toContain("‮");
  });

  it("acota nombres larguísimos y usa uno por defecto si falta", async () => {
    const long = await start((url) =>
      url === "/api/v1/session"
        ? jsonResponse({ ...SESSION, user: { ...SESSION.user, display_name: "N".repeat(5000) } })
        : problemResponse(404, "not-found"),
    );
    expect(long.session.displayName).toHaveLength(80);
    const none = await start((url) =>
      url === "/api/v1/session"
        ? jsonResponse({ ...SESSION, user: { ...SESSION.user, display_name: null } })
        : problemResponse(404, "not-found"),
    );
    expect(none.session.displayName).toBe("Usuario");
  });
});
