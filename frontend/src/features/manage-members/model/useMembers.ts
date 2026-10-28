import { ref } from "vue";

import { useApi } from "@/shared/api";
import type { Role } from "@/shared/api";
import { useAction, useResource } from "@/shared/lib";

export const ROLES: readonly Role[] = ["VIEWER", "TRIAGER", "ADMIN"];

export function useMembers(organizationId: () => string) {
  const api = useApi();
  const list = useResource((signal) => api.members(organizationId(), { signal }));
  const pendingRemoval = ref<string | null>(null);

  const changeRole = useAction(async (userId: string, role: Role) => {
    await api.setMemberRole(organizationId(), userId, role);
    await list.reload();
  });
  const remove = useAction(async (userId: string) => {
    await api.removeMember(organizationId(), userId);
    pendingRemoval.value = null;
    await list.reload();
  });

  return {
    members: list.data,
    loading: list.loading,
    loadError: list.error,
    reload: list.reload,
    pendingRemoval,
    changeRole: changeRole.run,
    remove: remove.run,
    busy: () => changeRole.pending.value || remove.pending.value,
    actionError: () => changeRole.error.value ?? remove.error.value,
  };
}
