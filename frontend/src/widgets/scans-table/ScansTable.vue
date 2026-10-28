<script setup lang="ts">
import { RouterLink } from "vue-router";

import { ScanStatusTag, VerdictTag } from "@/entities/scan";
import type { Scan } from "@/shared/api";
import { formatDuration, formatTimestamp, isUuid, visibleText } from "@/shared/lib";
import { DataTable } from "@/shared/ui";
import type { Column } from "@/shared/ui";

defineProps<{ scans: readonly Scan[]; loading: boolean }>();

const columns: readonly Column<Scan>[] = [
  { key: "commit", header: "Commit", mono: true },
  { key: "ref", header: "Referencia", mono: true },
  { key: "status", header: "Estado" },
  { key: "verdict", header: "Quality gate" },
  { key: "trigger", header: "Origen", mono: true },
  { key: "queued_at", header: "En cola", mono: true, text: (s) => formatTimestamp(s.queued_at) },
  { key: "duration", header: "Duración", numeric: true, text: (s) => formatDuration(s.started_at, s.finished_at) },
];
</script>

<template>
  <DataTable
    caption="Escaneos"
    caption-hidden
    bare
    :columns="columns"
    :rows="scans"
    :row-key="(s: Scan) => s.id"
    :loading="loading"
    empty-text="Aún no hay escaneos de este repositorio."
  >
    <template #cell-commit="{ row }">
      <RouterLink v-if="isUuid(row.id)" class="scans-table__commit" :to="{ name: 'scan', params: { scanId: row.id } }">
        {{ visibleText(row.commit.slice(0, 12), 12) }}
      </RouterLink>
      <template v-else>{{ visibleText(row.commit.slice(0, 12), 12) }}</template>
    </template>
    <template #cell-status="{ row }">
      <ScanStatusTag :status="row.status" />
    </template>
    <template #cell-verdict="{ row }">
      <VerdictTag :verdict="row.summary?.verdict" />
    </template>
  </DataTable>
</template>

<style scoped>
.scans-table__commit {
  font-weight: 600;
  text-decoration: none;
}

.scans-table__commit:hover {
  text-decoration: underline;
}
</style>
