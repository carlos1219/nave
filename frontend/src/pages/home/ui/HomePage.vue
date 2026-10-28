<script setup lang="ts">
import { onMounted } from "vue";
import { RouterLink } from "vue-router";

import { OpenFindingsSummary, totalCount, worstTone } from "@/entities/finding";
import { useOrganizations } from "@/entities/organization";
import type { Organization } from "@/shared/api";
import { isUuid, visibleText } from "@/shared/lib";
import { DataTable, ErrorNotice, PageLayout, StatusTag } from "@/shared/ui";
import type { Column } from "@/shared/ui";

const orgs = useOrganizations();
onMounted(() => void orgs.reload());

const columns: readonly Column<Organization>[] = [
  { key: "login", header: "Organización" },
  { key: "open_total", header: "Abiertas", numeric: true, text: (o) => String(totalCount(o.open_findings)) },
  { key: "open_findings", header: "Vulnerabilidades abiertas por severidad" },
  { key: "role", header: "Tu rol", mono: true },
  { key: "status", header: "Estado" },
];
</script>

<template>
  <PageLayout title="Organizaciones">
    <template v-if="orgs.organizations.value" #meta>
      <span>{{ orgs.organizations.value.length }} con acceso</span>
    </template>
    <ErrorNotice v-if="orgs.error.value" :error="orgs.error.value" />
    <p v-if="orgs.organizations.value?.length === 0" class="home__empty">
      No perteneces a ninguna organización. Las organizaciones aparecen cuando la GitHub App se instala en una cuenta
      y un administrador te da acceso.
    </p>
    <DataTable
      v-else
      caption="Organizaciones"
      caption-hidden
      :columns="columns"
      :rows="orgs.organizations.value ?? []"
      :row-key="(o: Organization) => o.id"
      :tone="(o: Organization) => worstTone(o.open_findings)"
      :loading="orgs.loading.value"
    >
      <template #cell-open_findings="{ row }">
        <OpenFindingsSummary :counts="row.open_findings" />
      </template>
      <template #cell-login="{ row }">
        <RouterLink v-if="isUuid(row.id)" class="home__link" :to="{ name: 'organization', params: { organizationId: row.id } }">
          {{ visibleText(row.login, 80) }}
        </RouterLink>
        <template v-else>{{ visibleText(row.login, 80) }}</template>
      </template>
      <template #cell-status="{ row }">
        <StatusTag :label="row.status === 'ACTIVE' ? 'Activa' : 'Suspendida'" :tone="row.status === 'ACTIVE' ? 'ok' : 'warn'" />
      </template>
    </DataTable>
  </PageLayout>
</template>

<style scoped>
.home__empty {
  padding: var(--space-7) var(--space-6);
  border: 1px dashed var(--line-strong);
  border-radius: var(--radius);
  background: var(--surface);
  color: var(--text-2);
  text-align: center;
}

.home__link {
  font-weight: 600;
  text-decoration: none;
}

.home__link:hover {
  text-decoration: underline;
}
</style>
