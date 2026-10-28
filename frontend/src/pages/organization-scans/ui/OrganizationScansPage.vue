<script setup lang="ts">
import { computed, onMounted } from "vue";
import { RouterLink } from "vue-router";

import { useOrganizationScans } from "@/entities/organization";
import { isTerminalStatus, ScanStatusTag, VerdictTag } from "@/entities/scan";
import type { ScanActivity } from "@/shared/api";
import { formatDuration, formatTimestamp, isUuid, visibleText } from "@/shared/lib";
import { DataTable, ErrorNotice, Panel } from "@/shared/ui";
import type { Column } from "@/shared/ui";
import { OrganizationShell } from "@/widgets/organization-shell";

const props = defineProps<{ organizationId: string }>();
const data = useOrganizationScans(() => props.organizationId);
onMounted(() => void data.reload());

const running = computed(() => (data.activity.value ?? []).filter((a) => !isTerminalStatus(a.scan.status)));
const finished = computed(() => (data.activity.value ?? []).filter((a) => isTerminalStatus(a.scan.status)));

const columns: readonly Column<ScanActivity>[] = [
  { key: "commit", header: "Commit", mono: true },
  { key: "repository", header: "Repositorio", mono: true },
  { key: "ref", header: "Referencia", mono: true, text: (a) => a.scan.ref },
  { key: "status", header: "Estado" },
  { key: "verdict", header: "Quality gate" },
  { key: "queued_at", header: "En cola", mono: true, text: (a) => formatTimestamp(a.scan.queued_at) },
  { key: "duration", header: "Duración", numeric: true, text: (a) => formatDuration(a.scan.started_at, a.scan.finished_at) },
];
</script>

<template>
  <OrganizationShell :organization-id="organizationId" section="Escaneos">
    <ErrorNotice v-if="data.error.value" :error="data.error.value" />
    <template v-for="block in [{ title: 'En curso', rows: running, empty: 'No hay escaneos en curso.' }, { title: 'Recientes', rows: finished, empty: 'Aún no hay escaneos terminados.' }]" :key="block.title">
      <Panel :title="block.title" flush>
        <template #aside>
          <span>{{ block.rows.length }}</span>
          <button v-if="block.title === 'En curso'" type="button" class="org-scans__refresh" :disabled="data.loading.value" @click="data.reload">
            Actualizar
          </button>
        </template>
        <DataTable
          :caption="`Escaneos ${block.title.toLowerCase()}`"
          caption-hidden
          bare
          :columns="columns"
          :rows="block.rows"
          :row-key="(a: ScanActivity) => a.scan.id"
          :loading="data.loading.value"
          :empty-text="block.empty"
        >
          <template #cell-commit="{ row }">
            <RouterLink v-if="isUuid(row.scan.id)" class="org-scans__link" :to="{ name: 'scan', params: { scanId: row.scan.id } }">
              {{ visibleText(row.scan.commit.slice(0, 12), 12) }}
            </RouterLink>
            <template v-else>{{ visibleText(row.scan.commit.slice(0, 12), 12) }}</template>
          </template>
          <template #cell-repository="{ row }">
            <RouterLink
              v-if="isUuid(row.repository.id)"
              class="org-scans__repo"
              :to="{ name: 'repository', params: { organizationId, repositoryId: row.repository.id } }"
            >
              {{ visibleText(row.repository.name, 100) }}
            </RouterLink>
            <template v-else>{{ visibleText(row.repository.name, 100) }}</template>
          </template>
          <template #cell-status="{ row }">
            <ScanStatusTag :status="row.scan.status" />
          </template>
          <template #cell-verdict="{ row }">
            <VerdictTag :verdict="row.scan.summary?.verdict" />
          </template>
        </DataTable>
      </Panel>
    </template>
  </OrganizationShell>
</template>

<style scoped>
.org-scans__refresh {
  height: 26px;
}

.org-scans__link {
  font-weight: 600;
  text-decoration: none;
}

.org-scans__repo {
  color: var(--text);
  text-decoration: none;
}

.org-scans__link:hover,
.org-scans__repo:hover {
  text-decoration: underline;
}
</style>
