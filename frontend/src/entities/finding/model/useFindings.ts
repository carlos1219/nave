import { watch } from "vue";
import type { Ref } from "vue";

import { useApi } from "@/shared/api";
import type { Finding, FindingFilter } from "@/shared/api";
import { usePagedResource } from "@/shared/lib";

const PAGE_SIZE = 100;

/** Findings de un repositorio con filtros en servidor; cambiar el filtro empieza de cero. */
export function useFindings(repositoryId: () => string, filter: Ref<FindingFilter>) {
  const api = useApi();
  const paged = usePagedResource<Finding>((cursor, signal) => {
    const query: FindingFilter = { ...filter.value, limit: PAGE_SIZE };
    if (cursor !== undefined) query.cursor = cursor;
    return api.findings(repositoryId(), query, { signal });
  });
  watch([repositoryId, filter], () => void paged.reset(), { immediate: true, deep: true });
  return { findings: paged.items, ...paged };
}
