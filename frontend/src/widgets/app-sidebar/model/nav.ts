import type { NavIconName } from "@/shared/ui";

export interface NavItem {
  /** Nombre de ruta: la lista es cerrada y nunca se construye desde datos del servidor. */
  name: string;
  label: string;
  icon: NavIconName;
  /** Solo se muestra a ADMIN. La autorización real es la del servidor. */
  adminOnly?: boolean;
}

export interface NavGroup {
  label: string;
  items: readonly NavItem[];
}

export const GENERAL_NAV: NavGroup = {
  label: "General",
  items: [
    { name: "home", label: "Organizaciones", icon: "organizations" },
    { name: "sessions", label: "Mis sesiones", icon: "sessions" },
  ],
};

export const ORGANIZATION_NAV: readonly NavGroup[] = [
  {
    label: "Panorama",
    items: [
      { name: "organization", label: "Resumen", icon: "overview" },
      { name: "organization-stats", label: "Estadísticas", icon: "stats" },
    ],
  },
  {
    label: "Triage",
    items: [
      { name: "organization-triage", label: "Cola de triage", icon: "queue" },
      { name: "organization-pulls", label: "Pull requests", icon: "pulls" },
      { name: "organization-risks", label: "Riesgos aceptados", icon: "risks" },
      { name: "organization-dependencies", label: "Dependencias", icon: "dependencies" },
    ],
  },
  {
    label: "Escaneos",
    items: [
      { name: "organization-scans", label: "Actividad", icon: "activity" },
      { name: "organization-failed", label: "Fallidos", icon: "failed" },
      { name: "organization-scanners", label: "Escáneres", icon: "scanners" },
    ],
  },
  {
    label: "Gobierno",
    items: [
      { name: "organization-rules", label: "Reglas", icon: "rules" },
      { name: "organization-policy", label: "Política", icon: "policy" },
      { name: "organization-audit", label: "Auditoría", icon: "audit", adminOnly: true },
    ],
  },
];

/** Solo se muestra a administradores de plataforma (ADR 0030). */
export const PLATFORM_NAV: NavGroup = {
  label: "Plataforma",
  items: [
    { name: "platform-users", label: "Usuarios", icon: "users" },
    { name: "platform-health", label: "Salud", icon: "health" },
    { name: "platform-webhooks", label: "Webhooks", icon: "webhooks" },
  ],
};

/** Rutas sin organización: su enlace no lleva parámetros. */
export const GLOBAL_ROUTES: ReadonlySet<string> = new Set([
  ...GENERAL_NAV.items.map((i) => i.name),
  ...PLATFORM_NAV.items.map((i) => i.name),
]);

/** Rutas hijas de otra: el enlace del padre se marca activo también en ellas. */
export const NAV_PARENT: Readonly<Record<string, string>> = {
  "pull-request": "organization-pulls",
  repository: "organization",
};

export function visibleGroups(isAdmin: boolean): NavGroup[] {
  return ORGANIZATION_NAV.map((group) => ({
    label: group.label,
    items: group.items.filter((item) => item.adminOnly !== true || isAdmin),
  }));
}
