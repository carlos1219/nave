import { computed, reactive } from "vue";

import { TRIAGE_STATES } from "@/entities/finding";
import type { FindingFilter, Severity, TriageState } from "@/shared/api";

export const FILTER_SEVERITIES: readonly Severity[] = ["CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"];
export const FILTER_STATES: readonly TriageState[] = TRIAGE_STATES;
const SCANNER = /^[a-z][a-z0-9-]{0,30}[a-z0-9]$/;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const MAX_SCANNERS = 8;

export interface FindingsFilterState {
  severity: Severity[];
  state: TriageState[];
  scanner: string[];
  onlyNew: boolean;
  seenInScan: string | null;
}

type Query = Readonly<Record<string, string | readonly (string | null)[] | null | undefined>>;

function many(value: Query[string]): string[] {
  if (value === undefined || value === null) return [];
  return (typeof value === "string" ? [value] : value).filter((v): v is string => v !== null);
}

function pick<T extends string>(values: string[], allowed: readonly T[]): T[] {
  return allowed.filter((candidate) => values.includes(candidate));
}

export function emptyFilter(): FindingsFilterState {
  return { severity: [], state: [], scanner: [], onlyNew: false, seenInScan: null };
}

/** Lee un filtro de la URL descartando todo lo que no esté en la lista cerrada. */
export function parseFilter(query: Query): FindingsFilterState {
  const seen = many(query["seen_in_scan"])[0];
  return {
    severity: pick(many(query["severity"]), FILTER_SEVERITIES),
    state: pick(many(query["state"]), FILTER_STATES),
    scanner: [...new Set(many(query["scanner"]).filter((s) => SCANNER.test(s)))].slice(0, MAX_SCANNERS),
    onlyNew: many(query["only_new"])[0] === "true",
    seenInScan: seen !== undefined && UUID.test(seen) ? seen.toLowerCase() : null,
  };
}

export function serializeFilter(filter: FindingsFilterState): Record<string, string | string[]> {
  const out: Record<string, string | string[]> = {};
  if (filter.severity.length > 0) out["severity"] = [...filter.severity];
  if (filter.state.length > 0) out["state"] = [...filter.state];
  if (filter.scanner.length > 0) out["scanner"] = [...filter.scanner];
  if (filter.onlyNew) out["only_new"] = "true";
  if (filter.seenInScan !== null) out["seen_in_scan"] = filter.seenInScan;
  return out;
}

export function toApiFilter(filter: FindingsFilterState): FindingFilter {
  const api: FindingFilter = {};
  if (filter.severity.length > 0) api.severity = filter.severity;
  if (filter.state.length > 0) api.state = filter.state;
  if (filter.scanner.length > 0) api.scanner = filter.scanner;
  if (filter.onlyNew) api.onlyNew = true;
  if (filter.seenInScan !== null) api.seenInScan = filter.seenInScan;
  return api;
}

function toggle<T>(list: T[], value: T): T[] {
  return list.includes(value) ? list.filter((v) => v !== value) : [...list, value];
}

export function useFindingsFilter(initial: FindingsFilterState = emptyFilter()) {
  const state = reactive<FindingsFilterState>({ ...initial });
  const apiFilter = computed(() => toApiFilter(state));
  const active = computed(() => Object.keys(serializeFilter(state)).length > 0);

  return {
    state,
    apiFilter,
    active,
    toggleSeverity: (value: Severity) => {
      state.severity = toggle(state.severity, value);
    },
    toggleState: (value: TriageState) => {
      state.state = toggle(state.state, value);
    },
    toggleScanner: (value: string) => {
      if (SCANNER.test(value)) state.scanner = toggle(state.scanner, value).slice(0, MAX_SCANNERS);
    },
    setOnlyNew: (value: boolean) => {
      state.onlyNew = value;
    },
    reset: () => {
      Object.assign(state, emptyFilter());
    },
  };
}
