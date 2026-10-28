import { ClientInputError } from "./errors";
import { buildPath, buildQuery } from "./http";
import type { Http, QueryValue } from "./http";
import type {
  AuditPage,
  CancelScanResult,
  ExportFormat,
  ExportView,
  FailedScan,
  Finding,
  FindingDetail,
  FindingPage,
  FindingRow,
  FindingRowPage,
  HealthReport,
  Me,
  PlatformOrganization,
  PlatformUser,
  PolicyRequest,
  WebhookActivity,
  Member,
  Organization,
  OrganizationStats,
  PolicyVersion,
  QualityGate,
  Repository,
  Role,
  RuleStat,
  Scan,
  ScanActivity,
  ScanLogList,
  ScannerReport,
  ScanPage,
  Session,
  SessionInfo,
  Severity,
  StartScanRequest,
  StartScanResult,
  TriageRequest,
  TriageResult,
  TriageState,
} from "./types";

const IDEMPOTENCY_KEY = /^[A-Za-z0-9._-]{16,128}$/;
const SCANNER = /^[a-z][a-z0-9-]{0,30}[a-z0-9]$/;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const MAX_REPEATED = 16;
const AUDIT_CURSOR = /^[0-9]{1,12}$/;
const QUEUE_CURSOR = /^[0-9]_[0-9]{1,18}_[0-9a-f]{32}$/;
const SESSION_ID = /^[0-9a-f]{32}$/;
type OrgView =
  | "stats"
  | "scans"
  | "accepted-risks"
  | "rules"
  | "dependencies"
  | "policy"
  | "audit-log"
  | "pull-requests"
  | "triage-queue"
  | "failed-scans"
  | "scanners"
  | "export";
const ORG_PATH = (view: OrgView): string => `/organizations/{organization_id}/${view}`;

export interface Paging {
  limit?: number;
  cursor?: string;
}

/** Filtros de la cola de triage y de las descargas. */
export interface QueueFilter extends Paging {
  severity?: readonly Severity[];
  scanner?: readonly string[];
  repositoryId?: string;
}

export interface ExportFilter {
  severity?: readonly Severity[];
  state?: readonly TriageState[];
  scanner?: readonly string[];
  repositoryId?: string;
}

function checkFilter(filter: { scanner?: readonly string[]; repositoryId?: string }): void {
  if (
    (filter.scanner ?? []).some((name) => !SCANNER.test(name)) ||
    (filter.scanner?.length ?? 0) > MAX_REPEATED ||
    (filter.repositoryId !== undefined && !UUID.test(filter.repositoryId))
  ) {
    throw new ClientInputError("el filtro no es válido");
  }
}

export interface FindingFilter extends Paging {
  severity?: readonly Severity[];
  state?: readonly TriageState[];
  scanner?: readonly string[];
  seenInScan?: string;
  onlyNew?: boolean;
}

type Signal = { signal?: AbortSignal | undefined };

export type Api = ReturnType<typeof createApi>;

/** Una función por operación del contrato. Ningún componente construye URLs ni llama a `fetch`. */
export function createApi(http: Http) {
  const get = <T>(
    path: string,
    params: Record<string, string> | undefined,
    query: Record<string, QueryValue> | undefined,
    signal: AbortSignal | undefined,
  ) => http.request<T>({ method: "GET", path, ...(params && { params }), ...(query && { query }), signal });

  return {
    async session(o: Signal = {}): Promise<Session> {
      return (await get<Session>("/session", undefined, undefined, o.signal)).data;
    },

    async logout(): Promise<void> {
      await http.request<undefined>({ method: "POST", path: "/auth/logout" });
    },

    async organizations(o: Signal = {}): Promise<Organization[]> {
      const reply = await get<{ items: Organization[] }>("/organizations", undefined, undefined, o.signal);
      return reply.data.items;
    },

    async repositories(organizationId: string, o: Signal = {}): Promise<Repository[]> {
      const reply = await get<{ items: Repository[] }>(
        "/organizations/{organization_id}/repositories",
        { organization_id: organizationId },
        undefined,
        o.signal,
      );
      return reply.data.items;
    },

    async members(organizationId: string, o: Signal = {}): Promise<Member[]> {
      const reply = await get<{ items: Member[] }>(
        "/organizations/{organization_id}/members",
        { organization_id: organizationId },
        undefined,
        o.signal,
      );
      return reply.data.items;
    },

    async setMemberRole(organizationId: string, userId: string, role: Role): Promise<void> {
      await http.request<undefined>({
        method: "PUT",
        path: "/organizations/{organization_id}/members/{user_id}",
        params: { organization_id: organizationId, user_id: userId },
        body: { role },
      });
    },

    async removeMember(organizationId: string, userId: string): Promise<void> {
      await http.request<undefined>({
        method: "DELETE",
        path: "/organizations/{organization_id}/members/{user_id}",
        params: { organization_id: organizationId, user_id: userId },
      });
    },

    async organizationStats(organizationId: string, o: Signal = {}): Promise<OrganizationStats> {
      return (await get<OrganizationStats>(ORG_PATH("stats"), { organization_id: organizationId }, undefined, o.signal))
        .data;
    },

    async organizationScans(organizationId: string, o: Signal = {}): Promise<ScanActivity[]> {
      const reply = await get<{ items: ScanActivity[] }>(
        ORG_PATH("scans"),
        { organization_id: organizationId },
        undefined,
        o.signal,
      );
      return reply.data.items;
    },

    async acceptedRisks(organizationId: string, o: Signal = {}): Promise<FindingRow[]> {
      const reply = await get<{ items: FindingRow[] }>(
        ORG_PATH("accepted-risks"),
        { organization_id: organizationId },
        undefined,
        o.signal,
      );
      return reply.data.items;
    },

    async ruleCatalog(organizationId: string, o: Signal = {}): Promise<RuleStat[]> {
      const reply = await get<{ items: RuleStat[] }>(
        ORG_PATH("rules"),
        { organization_id: organizationId },
        undefined,
        o.signal,
      );
      return reply.data.items;
    },

    async dependencyFindings(organizationId: string, o: Signal = {}): Promise<FindingRow[]> {
      const reply = await get<{ items: FindingRow[] }>(
        ORG_PATH("dependencies"),
        { organization_id: organizationId },
        undefined,
        o.signal,
      );
      return reply.data.items;
    },

    async policyHistory(organizationId: string, o: Signal = {}): Promise<PolicyVersion[]> {
      const reply = await get<{ items: PolicyVersion[] }>(
        ORG_PATH("policy"),
        { organization_id: organizationId },
        undefined,
        o.signal,
      );
      return reply.data.items;
    },

    async auditLog(organizationId: string, paging: Paging = {}, o: Signal = {}): Promise<AuditPage> {
      if (paging.cursor !== undefined && !AUDIT_CURSOR.test(paging.cursor)) {
        throw new ClientInputError("el cursor del registro no es válido");
      }
      const reply = await get<AuditPage>(
        ORG_PATH("audit-log"),
        { organization_id: organizationId },
        { limit: paging.limit, cursor: paging.cursor },
        o.signal,
      );
      return reply.data;
    },

    async pullRequests(organizationId: string, o: Signal = {}): Promise<ScanActivity[]> {
      const reply = await get<{ items: ScanActivity[] }>(
        ORG_PATH("pull-requests"),
        { organization_id: organizationId },
        undefined,
        o.signal,
      );
      return reply.data.items;
    },

    async triageQueue(organizationId: string, filter: QueueFilter = {}, o: Signal = {}): Promise<FindingRowPage> {
      checkFilter(filter);
      if (filter.cursor !== undefined && !QUEUE_CURSOR.test(filter.cursor)) {
        throw new ClientInputError("el cursor de la cola no es válido");
      }
      const reply = await get<FindingRowPage>(
        ORG_PATH("triage-queue"),
        { organization_id: organizationId },
        {
          severity: filter.severity,
          scanner: filter.scanner,
          repository_id: filter.repositoryId,
          limit: filter.limit,
          cursor: filter.cursor,
        },
        o.signal,
      );
      return reply.data;
    },

    async failedScans(organizationId: string, o: Signal = {}): Promise<FailedScan[]> {
      const reply = await get<{ items: FailedScan[] }>(
        ORG_PATH("failed-scans"),
        { organization_id: organizationId },
        undefined,
        o.signal,
      );
      return reply.data.items;
    },

    async scannerReport(organizationId: string, o: Signal = {}): Promise<ScannerReport> {
      return (await get<ScannerReport>(ORG_PATH("scanners"), { organization_id: organizationId }, undefined, o.signal))
        .data;
    },

    /** URL de descarga: se usa como `href` de un enlace (misma sesión por cookie, sin `fetch`). */
    organizationExportUrl(organizationId: string, view: ExportView, format: ExportFormat, filter: ExportFilter = {}): string {
      checkFilter(filter);
      return (
        buildPath(ORG_PATH("export"), { organization_id: organizationId }) +
        buildQuery({
          view,
          format,
          severity: filter.severity,
          scanner: filter.scanner,
          repository_id: filter.repositoryId,
        })
      );
    },

    repositoryExportUrl(repositoryId: string, format: ExportFormat, filter: ExportFilter = {}): string {
      checkFilter(filter);
      return (
        buildPath("/repositories/{repository_id}/findings/export", { repository_id: repositoryId }) +
        buildQuery({ format, severity: filter.severity, state: filter.state, scanner: filter.scanner })
      );
    },

    async mySessions(o: Signal = {}): Promise<SessionInfo[]> {
      const reply = await get<{ items: SessionInfo[] }>("/me/sessions", undefined, undefined, o.signal);
      return reply.data.items;
    },

    async revokeSession(sessionId: string): Promise<void> {
      if (!SESSION_ID.test(sessionId)) throw new ClientInputError("el identificador de sesión no es válido");
      // El identificador no es un UUID: va en la ruta tras validarse con su propio patrón.
      await http.request<undefined>({ method: "DELETE", path: `/me/sessions/${sessionId}` });
    },

    async revokeOtherSessions(): Promise<number> {
      const reply = await http.request<{ revoked: number }>({ method: "DELETE", path: "/me/sessions" });
      return reply.data.revoked;
    },

    async me(o: Signal = {}): Promise<Me> {
      return (await get<Me>("/me", undefined, undefined, o.signal)).data;
    },

    async updatePolicy(organizationId: string, policy: PolicyRequest): Promise<number> {
      const reply = await http.request<{ version: number }>({
        method: "PUT",
        path: ORG_PATH("policy"),
        params: { organization_id: organizationId },
        body: policy,
      });
      return reply.data.version;
    },

    async platformUsers(o: Signal = {}): Promise<PlatformUser[]> {
      return (await get<{ items: PlatformUser[] }>("/platform/users", undefined, undefined, o.signal)).data.items;
    },

    async platformOrganizations(o: Signal = {}): Promise<PlatformOrganization[]> {
      const reply = await get<{ items: PlatformOrganization[] }>("/platform/organizations", undefined, undefined, o.signal);
      return reply.data.items;
    },

    async setPlatformMembership(userId: string, organizationId: string, role: Role | null): Promise<void> {
      const path = "/platform/users/{user_id}/memberships/{organization_id}";
      const params = { user_id: userId, organization_id: organizationId };
      await http.request<undefined>(
        role === null ? { method: "DELETE", path, params } : { method: "PUT", path, params, body: { role } },
      );
    },

    async revokeUserSessions(userId: string): Promise<number> {
      const reply = await http.request<{ revoked: number }>({
        method: "DELETE",
        path: "/platform/users/{user_id}/sessions",
        params: { user_id: userId },
      });
      return reply.data.revoked;
    },

    async platformHealth(o: Signal = {}): Promise<HealthReport> {
      return (await get<HealthReport>("/platform/health", undefined, undefined, o.signal)).data;
    },

    async platformWebhooks(o: Signal = {}): Promise<WebhookActivity> {
      return (await get<WebhookActivity>("/platform/webhooks", undefined, undefined, o.signal)).data;
    },

    async scans(repositoryId: string, paging: Paging = {}, o: Signal = {}): Promise<ScanPage> {
      const reply = await get<ScanPage>(
        "/repositories/{repository_id}/scans",
        { repository_id: repositoryId },
        { limit: paging.limit, cursor: paging.cursor },
        o.signal,
      );
      return reply.data;
    },

    async startScan(
      repositoryId: string,
      request: StartScanRequest,
      idempotencyKey: string,
    ): Promise<{ created: boolean; scan: StartScanResult }> {
      if (!IDEMPOTENCY_KEY.test(idempotencyKey)) {
        throw new ClientInputError("la clave de idempotencia no es válida");
      }
      const reply = await http.request<StartScanResult>({
        method: "POST",
        path: "/repositories/{repository_id}/scans",
        params: { repository_id: repositoryId },
        headers: { "Idempotency-Key": idempotencyKey },
        body: request,
      });
      return { created: reply.status === 202, scan: reply.data };
    },

    async scan(scanId: string, o: Signal = {}): Promise<Scan> {
      return (await get<Scan>("/scans/{scan_id}", { scan_id: scanId }, undefined, o.signal)).data;
    },

    async cancelScan(scanId: string): Promise<CancelScanResult> {
      const reply = await http.request<CancelScanResult>({
        method: "POST",
        path: "/scans/{scan_id}/cancel",
        params: { scan_id: scanId },
      });
      return reply.data;
    },

    async scanLogs(
      scanId: string,
      page: { afterSeq?: number; limit?: number } = {},
      o: Signal = {},
    ): Promise<ScanLogList> {
      const reply = await get<ScanLogList>(
        "/scans/{scan_id}/logs",
        { scan_id: scanId },
        { after_seq: page.afterSeq, limit: page.limit },
        o.signal,
      );
      return reply.data;
    },

    async qualityGate(scanId: string, o: Signal = {}): Promise<QualityGate> {
      return (
        await get<QualityGate>("/scans/{scan_id}/quality-gate", { scan_id: scanId }, undefined, o.signal)
      ).data;
    },

    async findings(repositoryId: string, filter: FindingFilter = {}, o: Signal = {}): Promise<FindingPage> {
      if (
        (filter.scanner ?? []).some((name) => !SCANNER.test(name)) ||
        (filter.scanner?.length ?? 0) > MAX_REPEATED ||
        (filter.seenInScan !== undefined && !UUID.test(filter.seenInScan))
      ) {
        throw new ClientInputError("el filtro de findings no es válido");
      }
      const reply = await get<FindingPage>(
        "/repositories/{repository_id}/findings",
        { repository_id: repositoryId },
        {
          severity: filter.severity,
          state: filter.state,
          scanner: filter.scanner,
          seen_in_scan: filter.seenInScan,
          only_new: filter.onlyNew,
          limit: filter.limit,
          cursor: filter.cursor,
        },
        o.signal,
      );
      return reply.data;
    },

    async finding(findingId: string, o: Signal = {}): Promise<FindingDetail> {
      return (
        await get<FindingDetail>("/findings/{finding_id}", { finding_id: findingId }, undefined, o.signal)
      ).data;
    },

    async triage(findingId: string, request: TriageRequest): Promise<TriageResult> {
      const reply = await http.request<TriageResult>({
        method: "POST",
        path: "/findings/{finding_id}/triage",
        params: { finding_id: findingId },
        body: request,
      });
      return reply.data;
    },
  };
}

export type { Finding };
