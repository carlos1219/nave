<script setup lang="ts">
import { onMounted } from "vue";

import { useAuditLog } from "@/entities/organization";
import type { AuditEntry } from "@/shared/api";
import { formatTimestamp, visibleText } from "@/shared/lib";
import { DataTable, ErrorNotice, LoadMore, Panel } from "@/shared/ui";
import type { Column } from "@/shared/ui";
import { OrganizationShell } from "@/widgets/organization-shell";

const props = defineProps<{ organizationId: string }>();
const log = useAuditLog(() => props.organizationId);
onMounted(() => void log.reset());

function actorOf(entry: AuditEntry): string {
  if (entry.actor_name) return visibleText(entry.actor_name, 80);
  return entry.actor ? `${entry.actor.slice(0, 8)}…` : "Sistema";
}

function detailsOf(entry: AuditEntry): string {
  return Object.entries(entry.details)
    .map(([k, v]) => `${visibleText(k, 64)}=${visibleText(v, 120)}`)
    .join("  ");
}

const columns: readonly Column<AuditEntry>[] = [
  { key: "occurred_at", header: "Fecha", mono: true, text: (e) => formatTimestamp(e.occurred_at) },
  { key: "action", header: "Acción", mono: true },
  { key: "actor", header: "Quién", text: actorOf },
  { key: "target", header: "Sobre", mono: true, text: (e) => `${e.target_type}:${e.target_id}` },
  { key: "details", header: "Detalles", mono: true, text: detailsOf },
];
</script>

<template>
  <OrganizationShell :organization-id="organizationId" section="Auditoría">
    <ErrorNotice v-if="log.error.value" :error="log.error.value" />
    <Panel title="Registro de auditoría" flush>
      <template #aside><span>Inmutable; lo más reciente primero</span></template>
      <DataTable
        caption="Registro de auditoría de la organización"
        caption-hidden
        bare
        :columns="columns"
        :rows="log.entries.value"
        :row-key="(e: AuditEntry) => String(e.id)"
        :loading="log.loading.value"
        empty-text="Todavía no hay acciones registradas."
      >
        <template #cell-action="{ row }">
          <span class="audit__action" :class="{ 'audit__action--denied': row.action.includes('denied') }">{{ visibleText(row.action, 64) }}</span>
        </template>
      </DataTable>
    </Panel>
    <LoadMore :has-more="log.hasMore.value" :loading="log.loadingMore.value" :capped="log.capped.value" @more="log.loadMore" />
  </OrganizationShell>
</template>

<style scoped>
.audit__action {
  color: var(--text);
  font-weight: 600;
}

.audit__action--denied {
  color: var(--danger);
}
</style>
