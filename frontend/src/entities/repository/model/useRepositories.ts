import { useApi } from "@/shared/api";
import type { Repository } from "@/shared/api";
import { useResource } from "@/shared/lib";

export function useRepositories(organizationId: () => string) {
  const api = useApi();
  const resource = useResource<Repository[]>((signal) =>
    api.repositories(organizationId(), { signal }),
  );
  return { repositories: resource.data, ...resource };
}
