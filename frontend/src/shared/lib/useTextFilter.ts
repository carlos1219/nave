import { computed, ref } from "vue";
import type { ComputedRef, Ref } from "vue";

const MAX_QUERY = 100;

/** Filtro de texto local, sin distinguir mayúsculas, sobre los campos que indique `fields`. */
export function useTextFilter<T>(
  rows: () => readonly T[],
  fields: (row: T) => readonly string[],
): { query: Ref<string>; filtered: ComputedRef<readonly T[]> } {
  const query = ref("");
  const filtered = computed(() => {
    const needle = query.value.slice(0, MAX_QUERY).trim().toLowerCase();
    if (needle === "") return rows();
    return rows().filter((row) => fields(row).some((field) => field.toLowerCase().includes(needle)));
  });
  return { query, filtered };
}
