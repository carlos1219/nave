import type { Finding, Severity, SeverityCounts } from "@/shared/api";

import { SEVERITIES, toneOf } from "./severity";

export interface SeverityCount {
  severity: Severity;
  count: number;
}

/** Solo las severidades con algún hallazgo, de mayor a menor. */
export function nonZeroCounts(counts: SeverityCounts): SeverityCount[] {
  return SEVERITIES.map((severity) => ({ severity, count: counts[toneOf(severity)] })).filter((c) => c.count > 0);
}

export function totalCount(counts: SeverityCounts): number {
  return SEVERITIES.reduce((sum, severity) => sum + counts[toneOf(severity)], 0);
}

/** Recuento por severidad de los hallazgos con triage `OPEN`. */
export function countOpen(findings: Iterable<Pick<Finding, "severity" | "triage_state">>): SeverityCounts {
  const counts: SeverityCounts = { critical: 0, high: 0, medium: 0, low: 0, info: 0 };
  for (const finding of findings) {
    if (finding.triage_state === "OPEN") counts[toneOf(finding.severity)] += 1;
  }
  return counts;
}

/** Segmentos para una barra apilada, de mayor a menor severidad. */
export function severitySegments(counts: SeverityCounts): { key: ReturnType<typeof toneOf>; value: number }[] {
  return SEVERITIES.map((severity) => ({ key: toneOf(severity), value: counts[toneOf(severity)] }));
}

/** Tono de la peor severidad presente; `undefined` si no hay ninguna. */
export function worstTone(counts: SeverityCounts): ReturnType<typeof toneOf> | undefined {
  const worst = nonZeroCounts(counts)[0];
  return worst === undefined ? undefined : toneOf(worst.severity);
}
