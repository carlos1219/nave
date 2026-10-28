import { useApi } from "@/shared/api";
import type {
  AuditEntry,
  FailedScan,
  FindingRow,
  OrganizationStats,
  PolicyVersion,
  QueueFilter,
  RuleStat,
  ScanActivity,
  ScannerReport,
} from "@/shared/api";
import { usePagedResource, useResource } from "@/shared/lib";

const AUDIT_PAGE = 50;
const QUEUE_PAGE = 50;

export function useOrganizationStats(organizationId: () => string) {
  const api = useApi();
  const resource = useResource<OrganizationStats>((signal) => api.organizationStats(organizationId(), { signal }));
  return { stats: resource.data, ...resource };
}

export function useOrganizationScans(organizationId: () => string) {
  const api = useApi();
  const resource = useResource<ScanActivity[]>((signal) => api.organizationScans(organizationId(), { signal }));
  return { activity: resource.data, ...resource };
}

export function useAcceptedRisks(organizationId: () => string) {
  const api = useApi();
  const resource = useResource<FindingRow[]>((signal) => api.acceptedRisks(organizationId(), { signal }));
  return { risks: resource.data, ...resource };
}

export function useRuleCatalog(organizationId: () => string) {
  const api = useApi();
  const resource = useResource<RuleStat[]>((signal) => api.ruleCatalog(organizationId(), { signal }));
  return { rules: resource.data, ...resource };
}

export function useDependencyFindings(organizationId: () => string) {
  const api = useApi();
  const resource = useResource<FindingRow[]>((signal) => api.dependencyFindings(organizationId(), { signal }));
  return { dependencies: resource.data, ...resource };
}

export function usePolicyHistory(organizationId: () => string) {
  const api = useApi();
  const resource = useResource<PolicyVersion[]>((signal) => api.policyHistory(organizationId(), { signal }));
  return { versions: resource.data, ...resource };
}

export function useAuditLog(organizationId: () => string) {
  const api = useApi();
  const paged = usePagedResource<AuditEntry>((cursor, signal) => {
    const paging = cursor === undefined ? { limit: AUDIT_PAGE } : { limit: AUDIT_PAGE, cursor };
    return api.auditLog(organizationId(), paging, { signal });
  });
  return { entries: paged.items, ...paged };
}

export function usePullRequests(organizationId: () => string) {
  const api = useApi();
  const resource = useResource<ScanActivity[]>((signal) => api.pullRequests(organizationId(), { signal }));
  return { pulls: resource.data, ...resource };
}

export function useFailedScans(organizationId: () => string) {
  const api = useApi();
  const resource = useResource<FailedScan[]>((signal) => api.failedScans(organizationId(), { signal }));
  return { failures: resource.data, ...resource };
}

export function useScannerReport(organizationId: () => string) {
  const api = useApi();
  const resource = useResource<ScannerReport>((signal) => api.scannerReport(organizationId(), { signal }));
  return { report: resource.data, ...resource };
}

/** Cola de triage paginada; `filter` se lee en cada página, así que un `reset()` aplica el actual. */
export function useTriageQueue(organizationId: () => string, filter: () => Omit<QueueFilter, "limit" | "cursor">) {
  const api = useApi();
  const paged = usePagedResource<FindingRow>((cursor, signal) => {
    const base = { ...filter(), limit: QUEUE_PAGE };
    return api.triageQueue(organizationId(), cursor === undefined ? base : { ...base, cursor }, { signal });
  });
  return { rows: paged.items, ...paged };
}
