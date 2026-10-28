/**
 * Iconos funcionales de navegación (ADR 0029): trazos en una rejilla de 24, sin relleno.
 * Lista cerrada: el nombre nunca viene de datos del servidor.
 */
export const NAV_ICONS = {
  organizations: ["M4 4h6v6H4z", "M14 4h6v6h-6z", "M4 14h6v6H4z", "M14 14h6v6h-6z"],
  sessions: ["M12 15a4 4 0 1 1-8 0a4 4 0 0 1 8 0z", "M11 12l9-9", "M16 7l3 3", "M14 9l2 2"],
  overview: ["M4 11l8-7 8 7", "M6 9.5V20h12V9.5", "M10 20v-5h4v5"],
  stats: ["M4 20h16", "M7 16v-5", "M12 16V6", "M17 16v-8"],
  queue: ["M4 13l3-8h10l3 8", "M4 13v6h16v-6", "M4 13h5l1 2h4l1-2h5"],
  pulls: [
    "M8 6a2 2 0 1 1-4 0a2 2 0 0 1 4 0z",
    "M8 18a2 2 0 1 1-4 0a2 2 0 0 1 4 0z",
    "M20 18a2 2 0 1 1-4 0a2 2 0 0 1 4 0z",
    "M6 8v8",
    "M18 16V9a3 3 0 0 0-3-3h-4",
    "M13 4l-2 2 2 2",
  ],
  risks: ["M12 3l7 3v5c0 4.5-3 7.5-7 10c-4-2.5-7-5.5-7-10V6z", "M9 12l2 2 4-4"],
  dependencies: ["M12 3l8 4v10l-8 4-8-4V7z", "M4 7l8 4 8-4", "M12 11v10"],
  activity: ["M3 12h4l3-7 4 14 3-7h4"],
  failed: ["M12 4l9 16H3z", "M12 10v4", "M12 17v.01"],
  scanners: [
    "M7 7h10v10H7z",
    "M10 3v4",
    "M14 3v4",
    "M10 17v4",
    "M14 17v4",
    "M3 10h4",
    "M3 14h4",
    "M17 10h4",
    "M17 14h4",
  ],
  rules: ["M6 4h9a3 3 0 0 1 3 3v13H9a3 3 0 0 1-3-3z", "M6 17a3 3 0 0 1 3-3h9"],
  policy: ["M12 4v16", "M8 20h8", "M5 7h14", "M5 7l-3 6h6z", "M19 7l-3 6h6z"],
  audit: ["M9 3h6v3H9z", "M7 5H5v16h14V5h-2", "M9 11h6", "M9 15h6"],
  users: [
    "M12 8a3 3 0 1 1-6 0a3 3 0 0 1 6 0z",
    "M3 20c0-3.3 2.7-6 6-6s6 2.7 6 6",
    "M15 5.2a3 3 0 0 1 0 5.6",
    "M17 14.3c2.3.8 4 3 4 5.7",
  ],
  health: ["M4 5h16v11H4z", "M8 20h8", "M12 16v4", "M7 11h2l1.5-3 3 5 1.5-2h2"],
  webhooks: ["M8 7a4 4 0 1 1 6.9 2.8", "M9 16H5", "M15 16h4a3 3 0 1 1-3 3", "M12 11l-4 5", "M12 11l4 5"],
  collapse: ["M15 6l-6 6 6 6"],
  expand: ["M9 6l6 6-6 6"],
} as const satisfies Record<string, readonly string[]>;

export type NavIconName = keyof typeof NAV_ICONS;
