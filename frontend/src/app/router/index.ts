import { createRouter, createWebHistory } from "vue-router";
import type { Router, RouterHistory } from "vue-router";

import { useSessionStore } from "@/entities/session";
import { FindingPage } from "@/pages/finding";
import { FindingsPage } from "@/pages/findings";
import { HomePage } from "@/pages/home";
import { LoginPage } from "@/pages/login";
import { NotFoundPage } from "@/pages/not-found";
import { OrganizationPage } from "@/pages/organization";
import { OrganizationAuditPage } from "@/pages/organization-audit";
import { OrganizationDependenciesPage } from "@/pages/organization-dependencies";
import { OrganizationFailedPage } from "@/pages/organization-failed";
import { OrganizationPolicyPage } from "@/pages/organization-policy";
import { OrganizationPullsPage } from "@/pages/organization-pulls";
import { OrganizationRisksPage } from "@/pages/organization-risks";
import { OrganizationRulesPage } from "@/pages/organization-rules";
import { OrganizationScannersPage } from "@/pages/organization-scanners";
import { OrganizationScansPage } from "@/pages/organization-scans";
import { OrganizationStatsPage } from "@/pages/organization-stats";
import { OrganizationTriagePage } from "@/pages/organization-triage";
import { PlatformHealthPage } from "@/pages/platform-health";
import { PlatformUsersPage } from "@/pages/platform-users";
import { PlatformWebhooksPage } from "@/pages/platform-webhooks";
import { PullRequestPage } from "@/pages/pull-request";
import { RepositoryPage } from "@/pages/repository";
import { ScanPage } from "@/pages/scan";
import { SessionsPage } from "@/pages/sessions";
import type { Api } from "@/shared/api";
import { UUID_ROUTE_PATTERN } from "@/shared/lib";

const id = (name: string): string => `:${name}(${UUID_ROUTE_PATTERN})`;

/** Pestañas de una organización: segmento fijo de la URL, nombre de ruta y página. */
const ORGANIZATION_VIEWS = [
  ["stats", "organization-stats", OrganizationStatsPage],
  ["scans", "organization-scans", OrganizationScansPage],
  ["accepted-risks", "organization-risks", OrganizationRisksPage],
  ["rules", "organization-rules", OrganizationRulesPage],
  ["dependencies", "organization-dependencies", OrganizationDependenciesPage],
  ["policy", "organization-policy", OrganizationPolicyPage],
  ["audit-log", "organization-audit", OrganizationAuditPage],
  ["triage", "organization-triage", OrganizationTriagePage],
  ["pull-requests", "organization-pulls", OrganizationPullsPage],
  ["failed-scans", "organization-failed", OrganizationFailedPage],
  ["scanners", "organization-scanners", OrganizationScannersPage],
] as const;

declare module "vue-router" {
  interface RouteMeta {
    public?: boolean;
  }
}

/** Lista cerrada de rutas (AM-706): nada se deriva de parámetros ni de URLs de retorno. */
export function createNaveRouter(api: Pick<Api, "session">, history?: RouterHistory): Router {
  const router = createRouter({
    history: history ?? createWebHistory(),
    routes: [
      { path: "/", name: "home", component: HomePage },
      { path: `/organizations/${id("organizationId")}`, name: "organization", component: OrganizationPage, props: true },
      ...ORGANIZATION_VIEWS.map(([segment, name, component]) => ({
        path: `/organizations/${id("organizationId")}/${segment}`,
        name,
        component,
        props: true,
      })),
      {
        path: `/organizations/${id("organizationId")}/pull-requests/${id("scanId")}`,
        name: "pull-request",
        component: PullRequestPage,
        props: true,
      },
      { path: "/sessions", name: "sessions", component: SessionsPage },
      // Administración de plataforma (ADR 0030): la autorización es del servidor (404 si no).
      { path: "/platform/users", name: "platform-users", component: PlatformUsersPage },
      { path: "/platform/health", name: "platform-health", component: PlatformHealthPage },
      { path: "/platform/webhooks", name: "platform-webhooks", component: PlatformWebhooksPage },
      {
        path: `/organizations/${id("organizationId")}/repositories/${id("repositoryId")}`,
        name: "repository",
        component: RepositoryPage,
        props: true,
      },
      { path: `/repositories/${id("repositoryId")}/findings`, name: "findings", component: FindingsPage, props: true },
      { path: `/scans/${id("scanId")}`, name: "scan", component: ScanPage, props: true },
      { path: `/findings/${id("findingId")}`, name: "finding", component: FindingPage, props: true },
      { path: "/login", name: "login", component: LoginPage, meta: { public: true } },
      {
        path: "/:pathMatch(.*)*",
        name: "not-found",
        component: NotFoundPage,
        meta: { public: true },
      },
    ],
  });

  router.beforeEach(async (to) => {
    const session = useSessionStore();
    await session.ensureLoaded(api);
    if (session.status === "anonymous" && to.meta.public !== true) {
      // Sin `next`/`redirect`: tras el login siempre se vuelve a "/" (AM-706).
      return { name: "login" };
    }
    if (session.status === "authenticated" && to.name === "login") return { name: "home" };
    return true;
  });

  return router;
}
