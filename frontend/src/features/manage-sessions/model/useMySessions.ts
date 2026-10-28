import { computed } from "vue";

import { useApi } from "@/shared/api";
import type { SessionInfo } from "@/shared/api";
import { useAction, useResource } from "@/shared/lib";

/** Sesiones abiertas del usuario y cierre a distancia. La actual se cierra con «Salir». */
export function useMySessions() {
  const api = useApi();
  const resource = useResource<SessionInfo[]>((signal) => api.mySessions({ signal }));
  const others = computed(() => (resource.data.value ?? []).filter((s) => !s.current));

  const revokeOne = useAction(async (id: string) => {
    await api.revokeSession(id);
    await resource.reload();
  });
  const revokeOthers = useAction(async () => {
    const revoked = await api.revokeOtherSessions();
    await resource.reload();
    return revoked;
  });

  return {
    sessions: resource.data,
    others,
    loading: resource.loading,
    error: resource.error,
    reload: resource.reload,
    revokeOne,
    revokeOthers,
  };
}
