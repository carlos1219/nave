import { ApiError, ClientInputError } from "./errors";

export const API_PREFIX = "/api/v1";
export const DEFAULT_TIMEOUT_MS = 30_000;
export const MAX_RETRY_AFTER_SECONDS = 3600;
const UNSAFE = new Set(["POST", "PUT", "PATCH", "DELETE"]);
const MAX_ERROR_FIELD = 200;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const PROBLEM_TYPE = /^urn:nave:problem:[a-z0-9-]{1,64}$/;
const REQUEST_ID = /^[0-9a-f]{32}$/;

export type Method = "GET" | "POST" | "PUT" | "DELETE";
/** Un parámetro repetible (`severity=HIGH&severity=LOW`) se da como lista. */
export type QueryValue = string | number | boolean | readonly string[] | undefined;

export interface RequestSpec {
  method: Method;
  /** Plantilla con `{nombre}`; cada parámetro debe ser un UUID. */
  path: string;
  params?: Readonly<Record<string, string>>;
  /** Lista cerrada de parámetros de consulta; los `undefined` se omiten. */
  query?: Readonly<Record<string, QueryValue>>;
  body?: unknown;
  headers?: Readonly<Record<string, string>>;
  signal?: AbortSignal | undefined;
  timeoutMs?: number;
}

export interface HttpOptions {
  /** Token CSRF de la sesión actual, o `undefined` si no hay. Solo vive en memoria. */
  csrfToken: () => string | undefined;
  /** Se llama una vez por cada respuesta 401 (sesión caducada o ausente). */
  onUnauthorized: () => void;
  fetchImpl?: typeof fetch;
}

export interface Reply<T> {
  status: number;
  data: T;
}

export function buildPath(template: string, params: Readonly<Record<string, string>> = {}): string {
  const used = new Set<string>();
  const path = template.replace(/\{([a-z_]+)\}/g, (_match, name: string) => {
    const value = params[name];
    if (value === undefined || !UUID.test(value)) {
      throw new ClientInputError(`el parámetro ${name} no es un identificador válido`);
    }
    used.add(name);
    return value.toLowerCase();
  });
  if (used.size !== Object.keys(params).length) {
    throw new ClientInputError("sobran parámetros de ruta");
  }
  return `${API_PREFIX}${path}`;
}

export function buildQuery(query: Readonly<Record<string, QueryValue>> | undefined): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(query ?? {})) {
    if (value === undefined) continue;
    if (typeof value === "object") {
      for (const item of value) search.append(key, item);
    } else {
      search.set(key, String(value));
    }
  }
  const text = search.toString();
  return text === "" ? "" : `?${text}`;
}

function mediaType(response: Response): string {
  return (response.headers.get("content-type") ?? "").split(";")[0]?.trim().toLowerCase() ?? "";
}

function retryAfter(response: Response): number | null {
  const raw = response.headers.get("retry-after");
  if (raw === null || !/^\d{1,6}$/.test(raw)) return null;
  return Math.min(Number(raw), MAX_RETRY_AFTER_SECONDS);
}

function field(value: unknown, fallback: string): string {
  return typeof value === "string" && value.length <= MAX_ERROR_FIELD ? value : fallback;
}

async function problemFrom(response: Response): Promise<ApiError> {
  const common = { status: response.status, retryAfter: retryAfter(response) };
  let title = `Error ${String(response.status)}`;
  let type = "urn:nave:problem:unknown";
  let requestId: string | null = null;
  if (mediaType(response) === "application/problem+json") {
    try {
      const body: unknown = await response.json();
      if (typeof body === "object" && body !== null) {
        const record = body as Record<string, unknown>;
        title = field(record["title"], title);
        const rawType = field(record["type"], type);
        type = PROBLEM_TYPE.test(rawType) ? rawType : type;
        const rawId = record["request_id"];
        requestId = typeof rawId === "string" && REQUEST_ID.test(rawId) ? rawId : null;
      }
    } catch {
      // Cuerpo ilegible: se usa el error genérico del estado.
    }
  }
  return new ApiError({ ...common, title, type, requestId });
}

export type Http = ReturnType<typeof createHttp>;

export function createHttp(options: HttpOptions) {
  const doFetch = options.fetchImpl ?? ((input, init) => fetch(input, init));

  async function request<T>(spec: RequestSpec): Promise<Reply<T>> {
    const url = buildPath(spec.path, spec.params) + buildQuery(spec.query);
    const headers: Record<string, string> = { Accept: "application/json", ...spec.headers };
    const init: RequestInit = {
      method: spec.method,
      headers,
      credentials: "same-origin",
      cache: "no-store",
      redirect: "error",
      referrerPolicy: "no-referrer",
    };
    if (UNSAFE.has(spec.method)) {
      const csrf = options.csrfToken();
      if (csrf !== undefined) headers["X-CSRF-Token"] = csrf;
    }
    if (spec.body !== undefined) {
      headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(spec.body);
    }
    const timeout = AbortSignal.timeout(spec.timeoutMs ?? DEFAULT_TIMEOUT_MS);
    init.signal = spec.signal === undefined ? timeout : AbortSignal.any([spec.signal, timeout]);

    let response: Response;
    try {
      response = await doFetch(url, init);
    } catch (error) {
      if (spec.signal?.aborted === true) throw error; // cancelación pedida por el llamante
      const timedOut = timeout.aborted;
      throw new ApiError({
        status: 0,
        type: timedOut ? "urn:nave:client:timeout" : "urn:nave:client:network",
        title: timedOut ? "Tiempo de espera agotado" : "Sin conexión con el servidor",
      });
    }
    if (response.status === 401) options.onUnauthorized();
    if (!response.ok) throw await problemFrom(response);
    if (response.status === 204) return { status: 204, data: undefined as T };
    if (mediaType(response) !== "application/json") {
      throw new ApiError({
        status: response.status,
        type: "urn:nave:client:invalid-response",
        title: "Respuesta no válida del servidor",
      });
    }
    try {
      return { status: response.status, data: (await response.json()) as T };
    } catch {
      throw new ApiError({
        status: response.status,
        type: "urn:nave:client:invalid-response",
        title: "Respuesta no válida del servidor",
      });
    }
  }

  return { request };
}
