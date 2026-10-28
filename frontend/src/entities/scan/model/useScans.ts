import { useApi } from "@/shared/api";
import { usePagedResource } from "@/shared/lib";
import type { Scan } from "@/shared/api";

const PAGE_SIZE = 50;

export function useScans(repositoryId: () => string) {
  const api = useApi();
  const paged = usePagedResource<Scan>((cursor, signal) => {
    const paging = cursor === undefined ? { limit: PAGE_SIZE } : { limit: PAGE_SIZE, cursor };
    return api.scans(repositoryId(), paging, { signal });
  });
  return { scans: paged.items, ...paged };
}
