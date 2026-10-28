import { getCurrentScope, onScopeDispose, ref, shallowRef } from "vue";
import type { Ref, ShallowRef } from "vue";

import type { ApiError } from "@/shared/api";

import { asApiError } from "./api-error";

export interface Page<T> {
  items: readonly T[];
  next_cursor: string | null;
}

export interface PagedResource<T> {
  items: ShallowRef<readonly T[]>;
  loading: Ref<boolean>;
  loadingMore: Ref<boolean>;
  error: Ref<ApiError | null>;
  hasMore: Ref<boolean>;
  /** Se alcanzó el tope de elementos en memoria y quedan más en el servidor. */
  capped: Ref<boolean>;
  /** Empieza de cero (p. ej. al cambiar un filtro). */
  reset: () => Promise<void>;
  loadMore: () => Promise<void>;
  cancel: () => void;
}

export const DEFAULT_MAX_ITEMS = 1000;

/** Paginación por cursor opaco del servidor, acumulativa y acotada en memoria. */
export function usePagedResource<T>(
  fetchPage: (cursor: string | undefined, signal: AbortSignal) => Promise<Page<T>>,
  options: { maxItems?: number } = {},
): PagedResource<T> {
  const maxItems = options.maxItems ?? DEFAULT_MAX_ITEMS;
  const items = shallowRef<readonly T[]>([]);
  const loading = ref(false);
  const loadingMore = ref(false);
  const error = ref<ApiError | null>(null);
  const hasMore = ref(false);
  const capped = ref(false);
  let cursor: string | null = null;
  let controller: AbortController | undefined;
  let generation = 0;

  function cancel(): void {
    generation += 1;
    controller?.abort();
    controller = undefined;
    loading.value = false;
    loadingMore.value = false;
  }

  async function fetchInto(first: boolean): Promise<void> {
    const mine = generation;
    const local = new AbortController();
    controller = local;
    error.value = null;
    try {
      const page = await fetchPage(first ? undefined : (cursor ?? undefined), local.signal);
      if (mine !== generation) return;
      const room = maxItems - (first ? 0 : items.value.length);
      const accepted = page.items.slice(0, Math.max(room, 0));
      items.value = first ? accepted : [...items.value, ...accepted];
      const full = items.value.length >= maxItems;
      cursor = page.next_cursor;
      capped.value = full && (page.next_cursor !== null || accepted.length < page.items.length);
      hasMore.value = page.next_cursor !== null && !full;
    } catch (failure) {
      if (mine !== generation) return;
      error.value = asApiError(failure);
    } finally {
      if (mine === generation) {
        loading.value = false;
        loadingMore.value = false;
        controller = undefined;
      }
    }
  }

  async function reset(): Promise<void> {
    cancel();
    items.value = [];
    cursor = null;
    hasMore.value = false;
    capped.value = false;
    loading.value = true;
    await fetchInto(true);
  }

  async function loadMore(): Promise<void> {
    if (loading.value || loadingMore.value || !hasMore.value) return;
    loadingMore.value = true;
    await fetchInto(false);
  }

  if (getCurrentScope() !== undefined) onScopeDispose(cancel);
  return { items, loading, loadingMore, error, hasMore, capped, reset, loadMore, cancel };
}
