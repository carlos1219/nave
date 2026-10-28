import { describe, expect, it } from "vitest";

import { ApiError, ClientInputError } from "@/shared/api";

import { harness, headersOf, ID_A, json, problem, REQUEST_ID } from "./helpers";

describe("peticiones", () => {
  it("usa la misma origen, sin caché, sin redirecciones y sin Referer", async () => {
    const { http, calls } = harness(() => json({ ok: true }));
    await http.request({ method: "GET", path: "/scans/{scan_id}", params: { scan_id: ID_A } });
    const call = calls[0];
    expect(call?.url).toBe(`/api/v1/scans/${ID_A}`);
    expect(call?.init).toMatchObject({
      method: "GET",
      credentials: "same-origin",
      cache: "no-store",
      redirect: "error",
      referrerPolicy: "no-referrer",
    });
  });

  it("envía el token CSRF solo en métodos no seguros", async () => {
    const { http, calls } = harness(() => json({}));
    await http.request({ method: "GET", path: "/session" });
    await http.request({ method: "POST", path: "/scans/{scan_id}/cancel", params: { scan_id: ID_A } });
    await http.request({ method: "DELETE", path: "/scans/{scan_id}", params: { scan_id: ID_A } });
    expect(headersOf(calls[0])["X-CSRF-Token"]).toBeUndefined();
    expect(headersOf(calls[1])["X-CSRF-Token"]).toBe("csrf-token-1");
    expect(headersOf(calls[2])["X-CSRF-Token"]).toBe("csrf-token-1");
  });

  it("no inventa un token cuando no hay sesión", async () => {
    const { http, calls } = harness(() => json({}), null);
    await http.request({ method: "POST", path: "/auth/logout" });
    expect(headersOf(calls[0])["X-CSRF-Token"]).toBeUndefined();
  });

  it("serializa el cuerpo como JSON", async () => {
    const { http, calls } = harness(() => json({}));
    await http.request({ method: "POST", path: "/auth/logout", body: { a: 1 } });
    expect(calls[0]?.init.body).toBe('{"a":1}');
    expect(headersOf(calls[0])["Content-Type"]).toBe("application/json");
  });

  it("codifica la consulta y omite lo no definido", async () => {
    const { http, calls } = harness(() => json({}));
    await http.request({
      method: "GET",
      path: "/session",
      query: { cursor: "a b&c=d", limit: 5, only_new: true, skip: undefined },
    });
    expect(calls[0]?.url).toBe("/api/v1/session?cursor=a+b%26c%3Dd&limit=5&only_new=true");
  });

  it("204 no tiene cuerpo", async () => {
    const { http } = harness(() => new Response(null, { status: 204 }));
    expect((await http.request({ method: "POST", path: "/auth/logout" })).status).toBe(204);
  });
});

describe("identificadores de ruta", () => {
  it.each([
    "../admin",
    "..",
    "a/b",
    "%2e%2e",
    `${ID_A}/x`,
    `${ID_A}?q=1`,
    "",
    "not-a-uuid",
  ])("rechaza %j antes de salir a la red", async (bad) => {
    const { http, calls } = harness(() => json({}));
    await expect(
      http.request({ method: "GET", path: "/scans/{scan_id}", params: { scan_id: bad } }),
    ).rejects.toBeInstanceOf(ClientInputError);
    expect(calls).toEqual([]);
  });

  it("rechaza parámetros que sobran o que faltan", async () => {
    const { http } = harness(() => json({}));
    await expect(
      http.request({ method: "GET", path: "/session", params: { x: ID_A } }),
    ).rejects.toBeInstanceOf(ClientInputError);
    await expect(
      http.request({ method: "GET", path: "/scans/{scan_id}" }),
    ).rejects.toBeInstanceOf(ClientInputError);
  });

  it("normaliza a minúsculas", async () => {
    const { http, calls } = harness(() => json({}));
    await http.request({ method: "GET", path: "/scans/{scan_id}", params: { scan_id: ID_A.toUpperCase() } });
    expect(calls[0]?.url).toBe(`/api/v1/scans/${ID_A}`);
  });
});

describe("errores", () => {
  async function failure(response: Response) {
    const h = harness(() => response);
    const error = await h.http.request({ method: "GET", path: "/session" }).catch((e: unknown) => e);
    return { error, ...h };
  }

  it("traduce problem+json a ApiError con campos de forma fija", async () => {
    const { error } = await failure(problem(404, "not-found"));
    expect(error).toBeInstanceOf(ApiError);
    const api = error as ApiError;
    expect([api.status, api.type, api.requestId, api.notFound]).toEqual([
      404,
      "urn:nave:problem:not-found",
      REQUEST_ID,
      true,
    ]);
  });

  it("avisa de cada 401", async () => {
    const { error, unauthorized } = await failure(problem(401, "unauthorized"));
    expect((error as ApiError).unauthorized).toBe(true);
    expect(unauthorized.count).toBe(1);
  });

  it("no avisa de otros errores", async () => {
    const { unauthorized } = await failure(problem(403, "forbidden"));
    expect(unauthorized.count).toBe(0);
  });

  it("lee y acota Retry-After", async () => {
    const small = await failure(problem(429, "too-many-requests", { "retry-after": "17" }));
    expect((small.error as ApiError).retryAfter).toBe(17);
    expect((small.error as ApiError).tooManyRequests).toBe(true);
    const huge = await failure(problem(503, "unavailable", { "retry-after": "999999" }));
    expect((huge.error as ApiError).retryAfter).toBe(3600);
    const junk = await failure(problem(429, "too-many-requests", { "retry-after": "Wed, 21 Oct" }));
    expect((junk.error as ApiError).retryAfter).toBeNull();
  });

  it("marca como transitorios 429, 503 y los fallos de red", async () => {
    expect(((await failure(problem(429, "x"))).error as ApiError).transient).toBe(true);
    expect(((await failure(problem(503, "x"))).error as ApiError).transient).toBe(true);
    expect(((await failure(problem(400, "x"))).error as ApiError).transient).toBe(false);
  });

  it("descarta tipos, títulos e identificadores con forma inesperada", async () => {
    const hostile = new Response(
      JSON.stringify({
        type: "javascript:alert(1)",
        title: "<img src=x onerror=alert(1)>".repeat(100),
        request_id: "<script>",
        stack: "Traceback…",
      }),
      { status: 500, headers: { "content-type": "application/problem+json" } },
    );
    const { error } = await failure(hostile);
    const api = error as ApiError;
    expect(api.type).toBe("urn:nave:problem:unknown");
    expect(api.message).toBe("Error 500");
    expect(api.requestId).toBeNull();
  });

  it("un error sin problem+json (p. ej. HTML de un proxy) no se refleja", async () => {
    const html = new Response("<html>502 Bad Gateway nginx/1.2.3</html>", {
      status: 502,
      headers: { "content-type": "text/html" },
    });
    const { error } = await failure(html);
    expect((error as ApiError).message).toBe("Error 502");
    expect((error as ApiError).type).toBe("urn:nave:problem:unknown");
  });

  it("exige JSON en las respuestas correctas", async () => {
    const { error } = await failure(
      new Response("<html></html>", { status: 200, headers: { "content-type": "text/html" } }),
    );
    expect((error as ApiError).type).toBe("urn:nave:client:invalid-response");
    const broken = await failure(
      new Response("{", { status: 200, headers: { "content-type": "application/json" } }),
    );
    expect((broken.error as ApiError).type).toBe("urn:nave:client:invalid-response");
  });

  it("convierte los fallos de red en ApiError transitorio sin detalle", async () => {
    const h = harness(() => {
      throw new TypeError("Failed to fetch https://internal.example/secret");
    });
    const error = (await h.http.request({ method: "GET", path: "/session" }).catch((e: unknown) => e)) as ApiError;
    expect([error.status, error.type, error.transient]).toEqual([0, "urn:nave:client:network", true]);
    expect(error.message).not.toContain("internal");
  });

  it("deja pasar la cancelación pedida por el llamante", async () => {
    const controller = new AbortController();
    const h = harness(() => {
      controller.abort();
      throw new DOMException("aborted", "AbortError");
    });
    const error = await h.http
      .request({ method: "GET", path: "/session", signal: controller.signal })
      .catch((e: unknown) => e);
    expect(error).toBeInstanceOf(DOMException);
    expect(error).not.toBeInstanceOf(ApiError);
  });

  it("la señal del llamante llega a fetch combinada con el tiempo máximo", async () => {
    const controller = new AbortController();
    const h = harness(() => json({}));
    await h.http.request({ method: "GET", path: "/session", signal: controller.signal });
    const signal = h.calls[0]?.init.signal;
    expect(signal).toBeDefined();
    controller.abort();
    expect(signal?.aborted).toBe(true);
  });
});
