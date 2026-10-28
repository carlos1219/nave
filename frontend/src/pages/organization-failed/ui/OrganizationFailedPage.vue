<script setup lang="ts">
import { computed, onMounted } from "vue";
import { RouterLink, useRouter } from "vue-router";

import { canTriage, useFailedScans } from "@/entities/organization";
import { reasonLabel, ScanStatusTag } from "@/entities/scan";
import { useRetryScan } from "@/features/retry-scan";
import type { FailedScan } from "@/shared/api";
import { formatTimestamp, isUuid, visibleText } from "@/shared/lib";
import { DataTable, ErrorNotice, Panel, StatusTag } from "@/shared/ui";
import type { Column } from "@/shared/ui";
import { OrganizationShell } from "@/widgets/organization-shell";

const props = defineProps<{ organizationId: string }>();
const router = useRouter();
const data = useFailedScans(() => props.organizationId);
onMounted(() => void data.reload());
const retry = useRetryScan((scanId) => {
  if (isUuid(scanId)) void router.push({ name: "scan", params: { scanId } });
});

const pending = computed(() => (data.failures.value ?? []).filter((f) => !f.recovered).length);
const columns: readonly Column<FailedScan>[] = [
  { key: "repository", header: "Repositorio", mono: true, text: (f) => f.repository.name },
  { key: "ref", header: "Rama", mono: true, text: (f) => f.scan.ref },
  { key: "commit", header: "Commit", mono: true },
  { key: "status", header: "Estado" },
  { key: "reason", header: "Causa", text: (f) => (f.scan.status_reason ? reasonLabel(f.scan.status_reason.code) : "—") },
  { key: "finished_at", header: "Terminó", mono: true, text: (f) => (f.scan.finished_at ? formatTimestamp(f.scan.finished_at) : "—") },
  { key: "after", header: "Después" },
  { key: "action", header: "Acción" },
];
</script>

<template>
  <OrganizationShell :organization-id="organizationId" section="Escaneos fallidos">
    <template #default="{ org }">
      <ErrorNotice v-if="data.error.value" :error="data.error.value" />
      <ErrorNotice v-if="retry.error.value" :error="retry.error.value" />
      <Panel title="Fallidos y agotados" flush>
        <template #aside>
          <span v-if="data.failures.value">{{ pending }} sin resolver de {{ data.failures.value.length }}</span>
          <button type="button" class="failed__refresh" :disabled="data.loading.value" @click="data.reload">Actualizar</button>
        </template>
        <p class="failed__hint">
          Un escaneo queda «Resuelto» cuando otro posterior de la misma rama terminó bien. Los que siguen sin resolver indican
          repositorios que no se están analizando.
        </p>
        <DataTable
          caption="Escaneos fallidos o agotados, el más reciente primero"
          caption-hidden
          bare
          :columns="columns"
          :rows="data.failures.value ?? []"
          :row-key="(f: FailedScan) => f.scan.id"
          :tone="(f: FailedScan) => (f.recovered ? undefined : 'critical')"
          :loading="data.loading.value"
          empty-text="Ningún escaneo ha fallado en esta organización."
        >
          <template #cell-commit="{ row }">
            <RouterLink v-if="isUuid(row.scan.id)" class="failed__link" :to="{ name: 'scan', params: { scanId: row.scan.id } }">
              {{ visibleText(row.scan.commit.slice(0, 12), 12) }}
            </RouterLink>
            <template v-else>{{ visibleText(row.scan.commit.slice(0, 12), 12) }}</template>
          </template>
          <template #cell-status="{ row }">
            <ScanStatusTag :status="row.scan.status" />
          </template>
          <template #cell-reason="{ row }">
            <span class="failed__reason">
              <span>{{ row.scan.status_reason ? reasonLabel(row.scan.status_reason.code) : "—" }}</span>
              <span v-if="row.scan.status_reason?.detail" class="failed__detail">{{ visibleText(row.scan.status_reason.detail, 256) }}</span>
            </span>
          </template>
          <template #cell-after="{ row }">
            <StatusTag :label="row.recovered ? 'Resuelto' : 'Sin resolver'" :tone="row.recovered ? 'ok' : 'warn'" />
          </template>
          <template #cell-action="{ row }">
            <button
              v-if="canTriage(org.role)"
              type="button"
              class="failed__retry"
              :disabled="retry.pending.value"
              @click="retry.retry(row.scan)"
            >
              {{ retry.retrying.value === row.scan.id ? "Relanzando…" : "Relanzar" }}
            </button>
            <span v-else class="failed__muted">Requiere TRIAGER</span>
          </template>
        </DataTable>
      </Panel>
    </template>
  </OrganizationShell>
</template>

<style scoped>
.failed__refresh,
.failed__retry {
  height: 26px;
}

.failed__hint {
  padding: var(--space-3) var(--space-5);
  border-bottom: var(--border);
  color: var(--text-3);
  font-size: var(--fs-xs);
}

.failed__link {
  font-weight: 600;
  text-decoration: none;
}

.failed__link:hover {
  text-decoration: underline;
}

.failed__reason {
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.failed__detail {
  overflow: hidden;
  color: var(--text-3);
  font-family: var(--font-mono);
  font-size: 11px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.failed__muted {
  color: var(--text-3);
  font-size: var(--fs-xs);
}
</style>
