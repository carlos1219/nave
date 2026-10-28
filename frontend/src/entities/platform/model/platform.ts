import { useApi } from "@/shared/api";
import type { HealthReport, Me, PlatformOrganization, PlatformUser, WebhookActivity } from "@/shared/api";
import { useResource } from "@/shared/lib";

/** Solo decide si se muestra la sección «Plataforma»; la autorización es del servidor. */
export function usePlatformAccess() {
  const api = useApi();
  const resource = useResource<Me>((signal) => api.me({ signal }));
  return { me: resource.data, ...resource };
}

export function usePlatformUsers() {
  const api = useApi();
  const resource = useResource<PlatformUser[]>((signal) => api.platformUsers({ signal }));
  return { users: resource.data, ...resource };
}

export function usePlatformOrganizations() {
  const api = useApi();
  const resource = useResource<PlatformOrganization[]>((signal) => api.platformOrganizations({ signal }));
  return { organizations: resource.data, ...resource };
}

export function usePlatformHealth() {
  const api = useApi();
  const resource = useResource<HealthReport>((signal) => api.platformHealth({ signal }));
  return { report: resource.data, ...resource };
}

export function useWebhookActivity() {
  const api = useApi();
  const resource = useResource<WebhookActivity>((signal) => api.platformWebhooks({ signal }));
  return { activity: resource.data, ...resource };
}
