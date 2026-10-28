import type { Role } from "@/shared/api";

const RANK: Record<Role, number> = { VIEWER: 1, TRIAGER: 2, ADMIN: 3 };

export function hasRole(actual: Role, minimum: Role): boolean {
  return RANK[actual] >= RANK[minimum];
}

/** Solo para mostrar u ocultar controles. La autorización real es siempre la del servidor. */
export const canTriage = (role: Role): boolean => hasRole(role, "TRIAGER");
export const canAdminister = (role: Role): boolean => hasRole(role, "ADMIN");
