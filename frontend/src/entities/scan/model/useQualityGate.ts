import { useApi } from "@/shared/api";
import type { QualityGate } from "@/shared/api";
import { useResource } from "@/shared/lib";

export function useQualityGate(scanId: () => string) {
  const api = useApi();
  const resource = useResource<QualityGate>((signal) => api.qualityGate(scanId(), { signal }));
  return { gate: resource.data, ...resource };
}
