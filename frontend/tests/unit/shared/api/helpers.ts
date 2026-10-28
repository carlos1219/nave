import { createApi, createHttp } from "@/shared/api";

export interface Call {
  url: string;
  init: RequestInit;
}

export type Responder = (call: Call) => Response | Promise<Response>;

export const ID_A = "0f8fad5b-d9cb-469f-a165-70867728950e";
export const ID_B = "7c9e6679-7425-40de-944b-e07fc1f90ae7";
export const REQUEST_ID = "0123456789abcdef0123456789abcdef";

export function json(body: unknown, status = 200, headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json", ...headers },
  });
}

export function problem(status: number, slug: string, headers: Record<string, string> = {}): Response {
  return new Response(
    JSON.stringify({ type: `urn:nave:problem:${slug}`, title: "Fijo", status, request_id: REQUEST_ID }),
    { status, headers: { "content-type": "application/problem+json", ...headers } },
  );
}

export function harness(responder: Responder, csrf: string | null = "csrf-token-1") {
  const calls: Call[] = [];
  const unauthorized = { count: 0 };
  const fetchImpl = ((url: string, init: RequestInit) => {
    const call = { url, init };
    calls.push(call);
    return Promise.resolve(responder(call));
  }) as typeof fetch;
  const http = createHttp({
    csrfToken: () => csrf ?? undefined,
    onUnauthorized: () => {
      unauthorized.count += 1;
    },
    fetchImpl,
  });
  return { calls, unauthorized, http, api: createApi(http) };
}

export function headersOf(call: Call | undefined): Record<string, string> {
  return (call?.init.headers ?? {}) as Record<string, string>;
}
