<script setup lang="ts">
import { computed, onMounted } from "vue";

import { countOpen, FindingTitleLink, OpenFindingsSummary, toneOf } from "@/entities/finding";
import { useDependencyFindings } from "@/entities/organization";
import { ExportLinks } from "@/features/export-findings";
import type { FindingRow } from "@/shared/api";
import { useTextFilter, visibleText } from "@/shared/lib";
import { DataTable, ErrorNotice, Panel, SeverityBadge } from "@/shared/ui";
import type { Column } from "@/shared/ui";
import { OrganizationShell } from "@/widgets/organization-shell";

const props = defineProps<{ organizationId: string }>();
const data = useDependencyFindings(() => props.organizationId);
onMounted(() => void data.reload());

const search = useTextFilter(
  () => data.dependencies.value ?? [],
  (r) => [r.finding.title, ...r.finding.cve, r.repository.name],
);
const openCounts = computed(() => countOpen((data.dependencies.value ?? []).map((r) => r.finding)));

const columns: readonly Column<FindingRow>[] = [
  { key: "severity", header: "Severidad" },
  { key: "title", header: "Paquete y vulnerabilidad" },
  { key: "cve", header: "CVE", mono: true, text: (r) => r.finding.cve.join(", ") || "—" },
  { key: "repository", header: "Repositorio", mono: true, text: (r) => r.repository.name },
  { key: "triage", header: "Triage", mono: true, text: (r) => r.finding.triage_state },
];
</script>

<template>
  <OrganizationShell :organization-id="organizationId" section="Dependencias">
    <ErrorNotice v-if="data.error.value" :error="data.error.value" />
    <Panel title="Dependencias vulnerables" flush>
      <template #aside>
        <ExportLinks :source="{ kind: 'organization', organizationId, view: 'dependencies' }" />
        <label class="deps__search">
          <span class="visually-hidden">Buscar dependencia o CVE</span>
          <input v-model="search.query.value" type="search" maxlength="100" placeholder="Buscar paquete, CVE o repositorio" autocomplete="off" spellcheck="false">
        </label>
      </template>
      <div class="deps__summary">
        <span class="deps__summary-label">Abiertas</span>
        <OpenFindingsSummary :counts="openCounts" />
      </div>
      <DataTable
        caption="Dependencias vulnerables detectadas por el análisis de composición (SCA)"
        caption-hidden
        bare
        :columns="columns"
        :rows="search.filtered.value"
        :row-key="(r: FindingRow) => r.finding.id"
        :tone="(r: FindingRow) => (r.finding.triage_state === 'OPEN' ? toneOf(r.finding.severity) : undefined)"
        :loading="data.loading.value"
        :empty-text="search.query.value ? 'Ninguna dependencia coincide con la búsqueda.' : 'No se han detectado dependencias vulnerables.'"
      >
        <template #cell-severity="{ row }">
          <SeverityBadge :severity="row.finding.severity" />
        </template>
        <template #cell-title="{ row }">
          <FindingTitleLink :finding="row.finding" />
        </template>
        <template #cell-cve="{ row }">
          <span class="deps__cve">{{ row.finding.cve.map((c) => visibleText(c, 32)).join(", ") || "—" }}</span>
        </template>
      </DataTable>
    </Panel>
  </OrganizationShell>
</template>

<style scoped>
.deps__search input {
  width: 280px;
  max-width: 100%;
  min-height: 28px;
}

.deps__summary {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-4);
  padding: var(--space-3) var(--space-5);
  border-bottom: var(--border);
}

.deps__summary-label {
  color: var(--text-2);
  font-size: var(--fs-sm);
}

.deps__cve {
  color: var(--text);
  font-weight: 600;
}
</style>
