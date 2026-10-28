<script setup lang="ts">
import { onMounted } from "vue";
import { RouterLink } from "vue-router";

import { OpenFindingsSummary, totalCount } from "@/entities/finding";
import { usePullRequests } from "@/entities/organization";
import { ScanStatusTag, VerdictTag } from "@/entities/scan";
import type { ScanActivity } from "@/shared/api";
import { formatTimestamp, isUuid, visibleText } from "@/shared/lib";
import { DataTable, ErrorNotice, Panel } from "@/shared/ui";
import type { Column } from "@/shared/ui";
import { OrganizationShell } from "@/widgets/organization-shell";

const props = defineProps<{ organizationId: string }>();
const data = usePullRequests(() => props.organizationId);
onMounted(() => void data.reload());

const columns: readonly Column<ScanActivity>[] = [
  { key: "number", header: "PR", mono: true },
  { key: "repository", header: "Repositorio", mono: true, text: (a) => a.repository.name },
  { key: "ref", header: "Rama", mono: true, text: (a) => a.scan.ref },
  { key: "status", header: "Escaneo" },
  { key: "verdict", header: "Quality gate" },
  { key: "new", header: "Hallazgos nuevos" },
  { key: "queued_at", header: "Último escaneo", mono: true, text: (a) => formatTimestamp(a.scan.queued_at) },
];
</script>

<template>
  <OrganizationShell :organization-id="organizationId" section="Pull requests">
    <ErrorNotice v-if="data.error.value" :error="data.error.value" />
    <Panel title="Pull requests escaneadas" flush>
      <template #aside>
        <span v-if="data.pulls.value">{{ data.pulls.value.length }}</span>
        <button type="button" class="pulls__refresh" :disabled="data.loading.value" @click="data.reload">Actualizar</button>
      </template>
      <p class="pulls__hint">El último escaneo de cada PR. «Hallazgos nuevos» son los que la PR introduce respecto a la rama por defecto.</p>
      <DataTable
        caption="Pull requests con su último escaneo"
        caption-hidden
        bare
        :columns="columns"
        :rows="data.pulls.value ?? []"
        :row-key="(a: ScanActivity) => a.scan.id"
        :loading="data.loading.value"
        empty-text="Aún no se ha escaneado ninguna pull request de esta organización."
      >
        <template #cell-number="{ row }">
          <RouterLink
            v-if="isUuid(row.scan.id)"
            class="pulls__link"
            :to="{ name: 'pull-request', params: { organizationId, scanId: row.scan.id } }"
          >
            #{{ row.scan.pull_request_number }}
          </RouterLink>
          <template v-else>#{{ row.scan.pull_request_number }}</template>
        </template>
        <template #cell-repository="{ row }">{{ visibleText(row.repository.name, 100) }}</template>
        <template #cell-status="{ row }">
          <ScanStatusTag :status="row.scan.status" />
        </template>
        <template #cell-verdict="{ row }">
          <VerdictTag :verdict="row.scan.summary?.verdict" />
        </template>
        <template #cell-new="{ row }">
          <OpenFindingsSummary v-if="row.scan.summary && totalCount(row.scan.summary.new) > 0" :counts="row.scan.summary.new" />
          <span v-else-if="row.scan.summary" class="pulls__none">Ninguno</span>
          <span v-else class="pulls__none">—</span>
        </template>
      </DataTable>
    </Panel>
  </OrganizationShell>
</template>

<style scoped>
.pulls__refresh {
  height: 26px;
}

.pulls__hint {
  padding: var(--space-3) var(--space-5);
  border-bottom: var(--border);
  color: var(--text-3);
  font-size: var(--fs-xs);
}

.pulls__link {
  font-weight: 700;
  text-decoration: none;
}

.pulls__link:hover {
  text-decoration: underline;
}

.pulls__none {
  color: var(--text-3);
  font-size: var(--fs-sm);
}
</style>
