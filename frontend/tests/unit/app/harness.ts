import { flushPromises } from "@vue/test-utils";
import { createMemoryHistory } from "vue-router";

import { createNaveApp } from "@/app/create-app";
import { useSessionStore } from "@/entities/session";

export const SESSION = {
  csrf_token: "csrf-in-memory-only",
  expires_at: "2026-10-13T08:00:00Z",
  user: { id: "0f8fad5b-d9cb-469f-a165-70867728950e", display_name: "Alicia" },
};

export function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

export function problemResponse(status: number, slug: string): Response {
  return new Response(
    JSON.stringify({ type: `urn:nave:problem:${slug}`, title: "x", status, request_id: null }),
    { status, headers: { "content-type": "application/problem+json" } },
  );
}

export type Responder = (url: string, init: RequestInit) => Response | Promise<Response>;

/** Arranca la aplicación entera sobre un `fetch` simulado y espera a que se asiente. */
export async function boot(respond: Responder, path = "/") {
  const calls: { url: string; init: RequestInit }[] = [];
  const fetchImpl = ((url: string, init: RequestInit) => {
    calls.push({ url, init });
    return Promise.resolve(respond(url, init));
  }) as typeof fetch;
  const { app, router, pinia } = createNaveApp({ fetchImpl, history: createMemoryHistory() });
  const host = document.createElement("div");
  document.body.append(host);
  await router.push(path);
  app.mount(host);
  await flushPromises();
  return {
    host,
    router,
    pinia,
    calls,
    session: useSessionStore(pinia),
    path: () => router.currentRoute.value.fullPath,
    text: () => host.textContent,
    button: (label: string) =>
      [...host.querySelectorAll("button")].find((b) => b.textContent.trim() === label),
    unmount: () => {
      app.unmount();
      host.remove();
    },
  };
}
