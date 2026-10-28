import { computed, reactive } from "vue";

import { SEVERITIES } from "@/entities/finding";
import type { ExportFilter, QueueFilter, Severity } from "@/shared/api";
import { isUuid } from "@/shared/lib";

export interface QueueFilterState {
  severity: Severity[];
  repositoryId: string;
}

/** Filtro de la cola: severidades marcadas y, opcionalmente, un repositorio de la organización. */
export function useQueueFilter() {
  const state = reactive<QueueFilterState>({ severity: [], repositoryId: "" });

  const query = computed<Omit<QueueFilter, "limit" | "cursor">>(() => {
    const filter: Omit<QueueFilter, "limit" | "cursor"> = {};
    if (state.severity.length > 0) filter.severity = [...state.severity];
    if (isUuid(state.repositoryId)) filter.repositoryId = state.repositoryId;
    return filter;
  });
  const exportFilter = computed<ExportFilter>(() => ({ ...query.value }));
  const active = computed(() => state.severity.length > 0 || state.repositoryId !== "");

  function toggleSeverity(value: Severity): void {
    state.severity = state.severity.includes(value)
      ? state.severity.filter((s) => s !== value)
      : SEVERITIES.filter((s) => s === value || state.severity.includes(s));
  }

  function reset(): void {
    state.severity = [];
    state.repositoryId = "";
  }

  return { state, query, exportFilter, active, toggleSeverity, reset };
}
