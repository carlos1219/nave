import type { StatusTone } from "@/shared/ui";

import { visibleText } from "@/shared/lib";

const NAMES: Readonly<Record<string, string>> = {
  database: "Base de datos",
  broker: "Broker (Redis)",
  "queue-scan": "Cola de escaneos",
  "queue-report": "Cola de informes",
  "queue-maintenance": "Cola de mantenimiento",
  "scan-runner": "scan-runner",
  "egress-proxy": "Proxy de salida",
  worker: "Worker y scheduler",
};
const STATUS: Readonly<Record<string, { label: string; tone: StatusTone }>> = {
  OK: { label: "Operativo", tone: "ok" },
  DEGRADED: { label: "Degradado", tone: "warn" },
  DOWN: { label: "Caído", tone: "fail" },
};

/** Nombre legible de un componente; lo desconocido se muestra acotado. */
export function componentName(component: string): string {
  return Object.hasOwn(NAMES, component) ? (NAMES[component] ?? component) : visibleText(component, 32);
}

export function healthStatus(status: string): { label: string; tone: StatusTone } {
  return Object.hasOwn(STATUS, status) ? (STATUS[status] ?? { label: status, tone: "neutral" }) : { label: visibleText(status, 16), tone: "neutral" };
}

/** El peor estado manda: un componente caído hace que el resumen sea «Caído». */
export function overallStatus(statuses: readonly string[]): string {
  if (statuses.includes("DOWN")) return "DOWN";
  if (statuses.includes("DEGRADED")) return "DEGRADED";
  return statuses.length > 0 ? "OK" : "DEGRADED";
}
