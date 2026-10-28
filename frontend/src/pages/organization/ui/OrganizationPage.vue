<script setup lang="ts">
import { computed, onMounted } from "vue";
import { RouterLink } from "vue-router";

import { OpenFindingsSummary, totalCount, worstTone } from "@/entities/finding";
import { canAdminister } from "@/entities/organization";
import { useRepositories } from "@/entities/repository";
import { useSessionStore } from "@/entities/session";
import { MembersPanel } from "@/features/manage-members";
import type { Repository } from "@/shared/api";
import { isUuid, visibleText } from "@/shared/lib";
import { DataTable, ErrorNotice, Panel, StatusTag } from "@/shared/ui";
import type { Column } from "@/shared/ui";
import { OrganizationShell } from "@/widgets/organization-shell";

const props = defineProps<{ organizationId: string }>();
const session = useSessionStore();
const repos = useRepositories(() => props.organizationId);

const columns: readonly Column<Repository>[] = [
  { key: "name", header: "Repositorio" },
  { key: "open_total", header: "Abiertas", numeric: true, text: (r) => String(totalCount(r.open_findings)) },
  { key: "open_findings", header: "Vulnerabilidades abiertas por severidad" },
  { key: "default_branch", header: "Rama por defecto", mono: true },
  { key: "enabled", header: "Estado" },
];
const totals = computed(() => (repos.repositories.value ?? []).reduce((sum, r) => sum + totalCount(r.open_findings), 0));

onMounted(() => void repos.reload());
</script>

<template>
  <OrganizationShell v-slot="{ org }" :organization-id="organizationId" section="Resumen">
    <ErrorNotice v-if="repos.error.value" :error="repos.error.value" />
    <Panel title="Repositorios" flush>
      <template v-if="repos.repositories.value" #aside>
        <span>{{ repos.repositories.value.length }} repositorios</span>
        <span>{{ totals }} vulnerabilidades abiertas</span>
      </template>
      <DataTable
        caption="Repositorios"
        caption-hidden
        bare
        :columns="columns"
        :rows="repos.repositories.value ?? []"
        :row-key="(r: Repository) => r.id"
        :tone="(r: Repository) => worstTone(r.open_findings)"
        :loading="repos.loading.value"
        empty-text="La GitHub App aún no tiene repositorios en esta organización."
      >
        <template #cell-name="{ row }">
          <RouterLink
            v-if="isUuid(row.id)"
            class="org__link"
            :to="{ name: 'repository', params: { organizationId, repositoryId: row.id } }"
          >
            {{ visibleText(row.owner, 80) }}/{{ visibleText(row.name, 100) }}
          </RouterLink>
          <span v-else class="org__repo">{{ visibleText(row.owner, 80) }}/{{ visibleText(row.name, 100) }}</span>
        </template>
        <template #cell-open_findings="{ row }">
          <OpenFindingsSummary :counts="row.open_findings" />
        </template>
        <template #cell-enabled="{ row }">
          <StatusTag :label="row.enabled ? 'Activo' : 'Desactivado'" :tone="row.enabled ? 'ok' : 'neutral'" />
        </template>
      </DataTable>
    </Panel>
    <Panel v-if="canAdminister(org.role)" title="Miembros" flush>
      <MembersPanel :organization-id="organizationId" :current-user-id="session.userId" />
    </Panel>
  </OrganizationShell>
</template>

<style scoped>
.org__link,
.org__repo {
  font-family: var(--font-mono);
  font-size: var(--fs-sm);
  font-weight: 600;
  text-decoration: none;
}

.org__link:hover {
  text-decoration: underline;
}
</style>
