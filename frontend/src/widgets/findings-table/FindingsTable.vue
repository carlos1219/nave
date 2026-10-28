<script setup lang="ts">
import { RouterLink } from "vue-router";

import { toneOf } from "@/entities/finding";
import type { Finding } from "@/shared/api";
import { isUuid, visibleText } from "@/shared/lib";
import { DataTable, SeverityBadge } from "@/shared/ui";
import type { Column } from "@/shared/ui";

defineProps<{ findings: readonly Finding[]; loading: boolean }>();

function where(finding: Finding): string {
  const location = finding.occurrence?.location;
  if (location === null || location === undefined) return "—";
  return location.start_line === null ? location.path : `${location.path}:${String(location.start_line)}`;
}

const columns: readonly Column<Finding>[] = [
  { key: "severity", header: "Severidad" },
  { key: "title", header: "Hallazgo" },
  { key: "location", header: "Ubicación", mono: true, text: where },
  { key: "scanner", header: "Escáner", mono: true },
  { key: "triage_state", header: "Triage", mono: true },
];
</script>

<template>
  <DataTable
    caption="Hallazgos"
    caption-hidden
    bare
    :columns="columns"
    :rows="findings"
    :row-key="(f: Finding) => f.id"
    :tone="(f: Finding) => toneOf(f.severity)"
    :loading="loading"
    empty-text="Ningún hallazgo coincide con el filtro."
  >
    <template #cell-severity="{ row }">
      <SeverityBadge :severity="row.severity" />
    </template>
    <template #cell-title="{ row }">
      <span class="findings-table__title">
        <RouterLink v-if="isUuid(row.id)" class="findings-table__link" :to="{ name: 'finding', params: { findingId: row.id } }">
          {{ visibleText(row.title, 200) }}
        </RouterLink>
        <template v-else>{{ visibleText(row.title, 200) }}</template>
        <span v-if="row.occurrence?.is_new" class="findings-table__new">Nuevo</span>
      </span>
      <span class="findings-table__rule">{{ visibleText(row.rule_id, 200) }}</span>
    </template>
  </DataTable>
</template>

<style scoped>
.findings-table__title {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  min-width: 0;
}

.findings-table__link {
  overflow: hidden;
  color: var(--text);
  font-weight: 600;
  text-decoration: none;
  text-overflow: ellipsis;
}

.findings-table__link:hover {
  color: var(--accent);
  text-decoration: underline;
}

.findings-table__new {
  flex-shrink: 0;
  padding: 0 6px;
  border: 1px solid var(--accent);
  border-radius: var(--radius-sm);
  color: var(--accent);
  font-size: 11px;
  font-weight: 600;
  line-height: 16px;
}

.findings-table__rule {
  display: block;
  overflow: hidden;
  color: var(--text-3);
  font-family: var(--font-mono);
  font-size: 11px;
  text-overflow: ellipsis;
}
</style>
