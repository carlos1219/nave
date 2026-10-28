<script setup lang="ts">
import { computed, onMounted } from "vue";

import { componentName, healthStatus, overallStatus, usePlatformHealth } from "@/entities/platform";
import type { ComponentHealth } from "@/shared/api";
import { formatTimestamp, visibleText } from "@/shared/lib";
import { DataTable, ErrorNotice, PageLayout, Panel, StatusTag } from "@/shared/ui";
import type { Column, Crumb } from "@/shared/ui";

const health = usePlatformHealth();
onMounted(() => void health.reload());

const components = computed(() => health.report.value?.components ?? []);
const overall = computed(() => healthStatus(overallStatus(components.value.map((c) => c.status))));
const counts = computed(() => ({
  ok: components.value.filter((c) => c.status === "OK").length,
  bad: components.value.filter((c) => c.status !== "OK").length,
}));

const trail: readonly Crumb[] = [{ label: "Organizaciones", to: { name: "home" } }, { label: "Salud del servidor" }];
const columns: readonly Column<ComponentHealth>[] = [
  { key: "component", header: "Componente", text: (c) => componentName(c.component) },
  { key: "status", header: "Estado" },
  { key: "detail", header: "Detalle", text: (c) => c.detail },
  { key: "latency_ms", header: "Latencia", numeric: true, text: (c) => (c.latency_ms === null ? "—" : `${String(c.latency_ms)} ms`) },
  { key: "checked_at", header: "Comprobado", mono: true, text: (c) => formatTimestamp(c.checked_at) },
];
</script>

<template>
  <PageLayout title="Salud del servidor" :trail="trail">
    <template #meta>
      <span>Administración de plataforma</span>
    </template>
    <template #actions>
      <button type="button" class="health__refresh" :disabled="health.loading.value" @click="health.reload">Comprobar ahora</button>
    </template>
    <ErrorNotice v-if="health.error.value" :error="health.error.value" />
    <section v-if="health.report.value" class="health__summary" aria-label="Resumen">
      <div class="health__card">
        <span class="health__label">Estado general</span>
        <StatusTag :label="overall.label" :tone="overall.tone" />
      </div>
      <div class="health__card">
        <span class="health__label">Componentes</span>
        <span class="health__value">{{ counts.ok }} operativos, {{ counts.bad }} con problemas</span>
      </div>
      <div class="health__card">
        <span class="health__label">Último latido del worker</span>
        <span class="health__value health__mono">
          {{ health.report.value.heartbeat_at ? formatTimestamp(health.report.value.heartbeat_at) : "Nunca" }}
        </span>
        <span v-if="health.report.value.stale" class="health__warning">
          Sin latido en los últimos 3 minutos: el worker o el scheduler no están funcionando.
        </span>
      </div>
    </section>
    <Panel title="Componentes" flush>
      <p class="health__hint">
        La API comprueba en vivo la base de datos, el broker y las colas. scan-runner y el proxy de salida los comprueba el worker cada
        minuto, porque la API no tiene red hacia ellos.
      </p>
      <DataTable
        caption="Estado de cada componente de NAVE"
        caption-hidden
        bare
        :columns="columns"
        :rows="components"
        :row-key="(c: ComponentHealth) => c.component"
        :tone="(c: ComponentHealth) => (c.status === 'DOWN' ? 'critical' : c.status === 'DEGRADED' ? 'medium' : undefined)"
        :loading="health.loading.value"
        empty-text="Sin datos de salud todavía."
      >
        <template #cell-status="{ row }">
          <StatusTag v-bind="healthStatus(row.status)" />
        </template>
        <template #cell-detail="{ row }">
          <span class="health__detail">{{ visibleText(row.detail, 256) }}</span>
        </template>
      </DataTable>
    </Panel>
  </PageLayout>
</template>

<style scoped>
.health__refresh {
  height: 30px;
}

.health__summary {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: var(--space-4);
}

.health__card {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: var(--space-2);
  padding: var(--space-4) var(--space-5);
  border: var(--border);
  border-radius: var(--radius);
  background: var(--surface);
  box-shadow: var(--shadow-sm);
}

.health__label {
  color: var(--text-2);
  font-size: var(--fs-sm);
}

.health__value {
  font-size: var(--fs-md);
  font-weight: 600;
}

.health__mono {
  font-family: var(--font-mono);
  font-variant-numeric: tabular-nums;
}

.health__warning {
  color: var(--danger);
  font-size: var(--fs-xs);
  font-weight: 600;
}

.health__hint {
  padding: var(--space-3) var(--space-5);
  border-bottom: var(--border);
  color: var(--text-3);
  font-size: var(--fs-xs);
}

.health__detail {
  color: var(--text-2);
  font-size: var(--fs-sm);
}
</style>
