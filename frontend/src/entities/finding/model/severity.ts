import type { Severity } from "@/shared/api";

export const SEVERITIES: readonly Severity[] = ["CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"];

const RANK: Record<Severity, number> = { CRITICAL: 5, HIGH: 4, MEDIUM: 3, LOW: 2, INFO: 1 };

/** Mayor severidad primero. */
export function bySeverityDesc(a: Severity, b: Severity): number {
  return RANK[b] - RANK[a];
}

export function toneOf(severity: Severity): "critical" | "high" | "medium" | "low" | "info" {
  return severity.toLowerCase() as "critical" | "high" | "medium" | "low" | "info";
}
