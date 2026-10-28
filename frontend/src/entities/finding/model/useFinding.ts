import { useApi } from "@/shared/api";
import type { FindingDetail } from "@/shared/api";
import { useResource } from "@/shared/lib";

export function useFinding(findingId: () => string) {
  const api = useApi();
  const resource = useResource<FindingDetail>((signal) => api.finding(findingId(), { signal }));
  return { finding: resource.data, ...resource };
}
