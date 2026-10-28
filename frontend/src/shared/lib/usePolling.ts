import { getCurrentScope, onScopeDispose, ref } from "vue";
import type { Ref } from "vue";

import type { ApiError } from "@/shared/api";

import { asApiError } from "./api-error";

export type PollState = "idle" | "polling" | "paused" | "done" | "failed";

export interface PollOptions<T> {
  tick: (signal: AbortSignal) => Promise<T>;
  /** Aplica el resultado; devuelve `true` si cambió algo (reinicia el backoff). */
  onResult: (value: T) => boolean;
  /** Estado terminal: se detiene el sondeo. */
  isDone: (value: T) => boolean;
  baseDelayMs?: number;
  maxDelayMs?: number;
  /** Fallos seguidos tolerados antes de rendirse. */
  maxConsecutiveErrors?: number;
}

export interface Poller {
  state: Ref<PollState>;
  error: Ref<ApiError | null>;
  start: () => void;
  stop: () => void;
  /** Tras `failed`, vuelve a intentarlo desde cero. */
  retry: () => void;
}

export const BASE_DELAY_MS = 2000;
export const MAX_DELAY_MS = 15_000;
export const MAX_CONSECUTIVE_ERRORS = 5;
const BACKOFF_FACTOR = 1.6;
const NO_RETRY = new Set([401, 403, 404]);

/**
 * Sondeo con backoff progresivo con tope, `AbortController`, pausa con la pestaña oculta,
 * parada en estado terminal, reintentos acotados y limpieza al destruir el ámbito (CLAUDE.md §14).
 */
export function usePolling<T>(options: PollOptions<T>): Poller {
  const base = options.baseDelayMs ?? BASE_DELAY_MS;
  const max = options.maxDelayMs ?? MAX_DELAY_MS;
  const maxErrors = options.maxConsecutiveErrors ?? MAX_CONSECUTIVE_ERRORS;
  const state = ref<PollState>("idle");
  const error = ref<ApiError | null>(null);
  let controller: AbortController | undefined;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let generation = 0;
  let delay = base;
  let failures = 0;

  function clear(): void {
    generation += 1;
    controller?.abort();
    controller = undefined;
    if (timer !== undefined) clearTimeout(timer);
    timer = undefined;
  }

  function schedule(ms: number): void {
    if (document.hidden) {
      state.value = "paused";
      return;
    }
    const mine = generation;
    timer = setTimeout(() => {
      timer = undefined;
      if (mine === generation) void run();
    }, ms);
  }

  async function run(): Promise<void> {
    const mine = generation;
    const local = new AbortController();
    controller = local;
    try {
      const value = await options.tick(local.signal);
      if (mine !== generation) return;
      failures = 0;
      error.value = null;
      const changed = options.onResult(value);
      if (options.isDone(value)) {
        state.value = "done";
        return;
      }
      delay = changed ? base : Math.min(Math.round(delay * BACKOFF_FACTOR), max);
      schedule(delay);
    } catch (failure) {
      if (mine !== generation) return;
      const apiError = asApiError(failure);
      if (apiError === null) return;
      error.value = apiError;
      failures += 1;
      if (NO_RETRY.has(apiError.status) || failures > maxErrors) {
        state.value = "failed";
        return;
      }
      const waitForServer = (apiError.retryAfter ?? 0) * 1000;
      schedule(Math.max(waitForServer, Math.min(base * BACKOFF_FACTOR ** failures, max)));
    }
  }

  function onVisibility(): void {
    if (!document.hidden && state.value === "paused") {
      state.value = "polling";
      clear();
      void run();
    }
  }

  function start(): void {
    if (state.value === "polling" || state.value === "paused") return;
    clear();
    delay = base;
    failures = 0;
    error.value = null;
    state.value = "polling";
    if (document.hidden) state.value = "paused";
    else void run();
  }

  function stop(): void {
    clear();
    if (state.value === "polling" || state.value === "paused") state.value = "idle";
  }

  function retry(): void {
    if (state.value !== "failed") return;
    state.value = "idle";
    start();
  }

  document.addEventListener("visibilitychange", onVisibility);
  if (getCurrentScope() !== undefined) {
    onScopeDispose(() => {
      clear();
      document.removeEventListener("visibilitychange", onVisibility);
    });
  }
  return { state, error, start, stop, retry };
}
