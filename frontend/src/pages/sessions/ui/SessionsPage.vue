<script setup lang="ts">
import { onMounted } from "vue";

import { useMySessions } from "@/features/manage-sessions";
import type { SessionInfo } from "@/shared/api";
import { formatTimestamp } from "@/shared/lib";
import { DataTable, ErrorNotice, PageLayout, Panel, StatusTag } from "@/shared/ui";
import type { Column, Crumb } from "@/shared/ui";

const sessions = useMySessions();
onMounted(() => void sessions.reload());

const trail: readonly Crumb[] = [{ label: "Organizaciones", to: { name: "home" } }, { label: "Mis sesiones" }];
const columns: readonly Column<SessionInfo>[] = [
  { key: "which", header: "Sesión" },
  { key: "created_at", header: "Iniciada", mono: true, text: (s) => formatTimestamp(s.created_at) },
  { key: "last_seen_at", header: "Última actividad", mono: true, text: (s) => formatTimestamp(s.last_seen_at) },
  { key: "expires_at", header: "Caduca", mono: true, text: (s) => formatTimestamp(s.expires_at) },
  { key: "action", header: "Acción" },
];
</script>

<template>
  <PageLayout title="Mis sesiones" :trail="trail">
    <template #actions>
      <button
        type="button"
        class="sessions__all"
        :disabled="sessions.others.value.length === 0 || sessions.revokeOthers.pending.value"
        @click="sessions.revokeOthers.run()"
      >
        Cerrar las demás sesiones
      </button>
    </template>
    <ErrorNotice v-if="sessions.error.value" :error="sessions.error.value" />
    <ErrorNotice v-if="sessions.revokeOne.error.value" :error="sessions.revokeOne.error.value" />
    <ErrorNotice v-if="sessions.revokeOthers.error.value" :error="sessions.revokeOthers.error.value" />
    <Panel title="Sesiones abiertas" flush>
      <template #aside>
        <span v-if="sessions.sessions.value">{{ sessions.sessions.value.length }}</span>
      </template>
      <p class="sessions__hint">
        Cada inicio de sesión en un navegador abre una sesión. Si ves una que no reconoces, ciérrala; esta sesión se cierra con «Salir».
      </p>
      <DataTable
        caption="Sesiones abiertas de tu usuario"
        caption-hidden
        bare
        :columns="columns"
        :rows="sessions.sessions.value ?? []"
        :row-key="(s: SessionInfo) => s.id"
        :loading="sessions.loading.value"
        empty-text="No hay sesiones abiertas."
      >
        <template #cell-which="{ row }">
          <StatusTag v-if="row.current" label="Esta sesión" tone="ok" />
          <span v-else class="sessions__id">{{ row.id.slice(0, 8) }}</span>
        </template>
        <template #cell-action="{ row }">
          <button
            v-if="!row.current"
            type="button"
            class="sessions__close"
            :disabled="sessions.revokeOne.pending.value"
            @click="sessions.revokeOne.run(row.id)"
          >
            Cerrar
          </button>
          <span v-else class="sessions__muted">Usa «Salir»</span>
        </template>
      </DataTable>
    </Panel>
  </PageLayout>
</template>

<style scoped>
.sessions__all,
.sessions__close {
  height: 28px;
}

.sessions__hint {
  padding: var(--space-3) var(--space-5);
  border-bottom: var(--border);
  color: var(--text-3);
  font-size: var(--fs-xs);
}

.sessions__id {
  font-family: var(--font-mono);
  font-size: var(--fs-xs);
  font-variant-numeric: tabular-nums;
}

.sessions__muted {
  color: var(--text-3);
  font-size: var(--fs-xs);
}
</style>
