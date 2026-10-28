import { useApi } from "@/shared/api";
import type { Role } from "@/shared/api";
import { useAction } from "@/shared/lib";

/**
 * Cambios de acceso de un usuario desde la administración de plataforma (ADR 0030): rol en
 * cualquier organización y cierre de todas sus sesiones. Tras cada cambio se recarga la lista.
 */
export function useUserAccess(onChanged: () => Promise<void> | void) {
  const api = useApi();

  const setRole = useAction(async (userId: string, organizationId: string, role: Role | null) => {
    await api.setPlatformMembership(userId, organizationId, role);
    await onChanged();
  });
  const revokeSessions = useAction(async (userId: string) => {
    const revoked = await api.revokeUserSessions(userId);
    await onChanged();
    return revoked;
  });

  const busy = (): boolean => setRole.pending.value || revokeSessions.pending.value;
  const error = () => setRole.error.value ?? revokeSessions.error.value;
  const clear = (): void => {
    setRole.clear();
    revokeSessions.clear();
  };

  return { setRole, revokeSessions, busy, error, clear };
}
