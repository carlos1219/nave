import { onScopeDispose, getCurrentScope, ref } from "vue";
import type { Ref } from "vue";

import type { ApiError } from "@/shared/api";

import { asApiError } from "./api-error";

export interface Action<A extends unknown[], R> {
  /** `undefined` si falló o si ya había una ejecución en curso (nunca se duplica un envío). */
  run: (...args: A) => Promise<R | undefined>;
  pending: Ref<boolean>;
  error: Ref<ApiError | null>;
  clear: () => void;
}

/** Escritura única: un solo envío a la vez, el error queda en estado y nada se pierde al destruir. */
export function useAction<A extends unknown[], R>(perform: (...args: A) => Promise<R>): Action<A, R> {
  const pending = ref(false);
  const error = ref<ApiError | null>(null);
  let alive = true;
  if (getCurrentScope() !== undefined) onScopeDispose(() => (alive = false));

  async function run(...args: A): Promise<R | undefined> {
    if (pending.value) return undefined;
    pending.value = true;
    error.value = null;
    try {
      return await perform(...args);
    } catch (failure) {
      if (alive) error.value = asApiError(failure);
      return undefined;
    } finally {
      if (alive) pending.value = false;
    }
  }

  return { run, pending, error, clear: () => (error.value = null) };
}
