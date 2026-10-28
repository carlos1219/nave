import { watch } from "vue";

import { useApi } from "@/shared/api";
import type { Finding, FindingFilter, Scan } from "@/shared/api";
import { usePagedResource, useResource } from "@/shared/lib";

const PAGE_SIZE = 100;

/**
 * Escaneo de una PR y solo los hallazgos que introduce: los vistos en ese escaneo cuyo
 * fingerprint no estaba en el baseline de la rama por defecto (`only_new`, CLAUDE.md §7).
 */
export function usePullRequest(scanId: () => string) {
  const api = useApi();
  const scan = useResource<Scan>((signal) => api.scan(scanId(), { signal }));
  const findings = usePagedResource<Finding>((cursor, signal) => {
    const current = scan.data.value;
    if (current === undefined) return Promise.resolve({ items: [], next_cursor: null });
    const query: FindingFilter = { seenInScan: current.id, onlyNew: true, limit: PAGE_SIZE };
    if (cursor !== undefined) query.cursor = cursor;
    return api.findings(current.repository_id, query, { signal });
  });
  watch(
    () => scan.data.value?.id,
    (id) => {
      if (id !== undefined) void findings.reset();
    },
  );
  return { scan, findings };
}
