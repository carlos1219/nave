import { getCurrentScope, onScopeDispose, ref, shallowRef } from "vue";
import type { Ref, ShallowRef } from "vue";

import type { ApiError } from "@/shared/api";

import { asApiError } from "./api-error";

export interface Resource<T> {
  data: ShallowRef<T | undefined>;
  error: Ref<ApiError | null>;
  loading: Ref<boolean>;
  /** Vuelve a pedir; una petición nueva cancela la anterior y la anterior nunca pisa a la nueva. */
  reload: () => Promise<void>;
  cancel: () => void;
}

/** Lectura única con cancelación, sin carreras y sin fugas al destruir el ámbito. */
export function useResource<T>(load: (signal: AbortSignal) => Promise<T>): Resource<T> {
  const data = shallowRef<T | undefined>(undefined);
  const error = ref<ApiError | null>(null);
  const loading = ref(false);
  let controller: AbortController | undefined;
  let generation = 0;

  function cancel(): void {
    generation += 1;
    controller?.abort();
    controller = undefined;
    loading.value = false;
  }

  async function reload(): Promise<void> {
    cancel();
    const mine = generation;
    const local = new AbortController();
    controller = local;
    loading.value = true;
    error.value = null;
    try {
      const value = await load(local.signal);
      if (mine !== generation) return;
      data.value = value;
    } catch (failure) {
      if (mine !== generation) return;
      error.value = asApiError(failure);
    } finally {
      if (mine === generation) {
        loading.value = false;
        controller = undefined;
      }
    }
  }

  if (getCurrentScope() !== undefined) onScopeDispose(cancel);
  return { data, error, loading, reload, cancel };
}
