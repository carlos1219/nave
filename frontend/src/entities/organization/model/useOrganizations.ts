import { useApi } from "@/shared/api";
import type { Organization } from "@/shared/api";
import { useResource } from "@/shared/lib";

export function useOrganizations() {
  const api = useApi();
  const resource = useResource<Organization[]>((signal) => api.organizations({ signal }));
  return { organizations: resource.data, ...resource };
}
