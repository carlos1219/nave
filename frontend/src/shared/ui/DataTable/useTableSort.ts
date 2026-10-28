import { ref } from "vue";

import type { SortState } from "./types";

/** Estado de ordenación: un clic ordena ascendente, otro descendente, y cambiar de columna reinicia. */
export function useTableSort(initial: SortState | null = null) {
  const sort = ref<SortState | null>(initial);

  function toggle(key: string): void {
    const current = sort.value;
    if (current?.key === key) {
      sort.value = { key, direction: current.direction === "asc" ? "desc" : "asc" };
    } else {
      sort.value = { key, direction: "asc" };
    }
  }

  return { sort, toggle };
}
