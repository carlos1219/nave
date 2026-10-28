<script setup lang="ts">
import { computed, onMounted } from "vue";

import { useWebhookActivity } from "@/entities/platform";
import type { WebhookActivity } from "@/shared/api";
import { formatTimestamp, visibleText } from "@/shared/lib";
import { DataTable, ErrorNotice, PageLayout, Panel, StatusTag } from "@/shared/ui";
import type { Column, Crumb, StatusTone } from "@/shared/ui";

import { byHour, REASON_LABELS, REASONS, totals } from "../model/buckets";
import type { HourRow } from "../model/buckets";

type Delivery = WebhookActivity["recent"][number];

const data = useWebhookActivity();
onMounted(() => void data.reload());

const OUTCOMES: Readonly<Record<string, { label: string; tone: StatusTone }>> = {
  ENQUEUED: { label: "Escaneo encolado", tone: "ok" },
  IGNORED: { label: "Ignorada", tone: "neutral" },
  REJECTED: { label: "Rechazada", tone: "fail" },
};
const outcome = (value: string) => OUTCOMES[value] ?? { label: visibleText(value, 16), tone: "neutral" as StatusTone };

const counters = computed(() => data.activity.value?.counters ?? []);
const reasonTotals = computed(() => totals(counters.value));
const hours = computed(() => byHour(counters.value));
const valid = computed(() => (data.activity.value?.outcomes ?? []).reduce((sum, o) => sum + o.count, 0));

const trail: readonly Crumb[] = [{ label: "Organizaciones", to: { name: "home" } }, { label: "Webhooks de GitHub" }];
const hourColumns: readonly Column<HourRow>[] = [
  { key: "hour", header: "Hora (UTC)", mono: true, text: (r) => r.hour.replace("T", " ") },
  ...REASONS.map((reason) => ({ key: reason, header: REASON_LABELS[reason], numeric: true, text: (r: HourRow) => String(r.counts[reason]) })),
  { key: "total", header: "Total", numeric: true },
];
const deliveryColumns: readonly Column<Delivery>[] = [
  { key: "received_at", header: "Recibida", mono: true, text: (d) => formatTimestamp(d.received_at) },
  { key: "event", header: "Evento", mono: true },
  { key: "outcome", header: "Resultado" },
  { key: "delivery_id", header: "Entrega (X-GitHub-Delivery)", mono: true },
];
</script>

<template>
  <PageLayout title="Webhooks de GitHub" :trail="trail">
    <template #meta>
      <span>Administración de plataforma</span>
    </template>
    <template #actions>
      <button type="button" class="hooks__refresh" :disabled="data.loading.value" @click="data.reload">Actualizar</button>
    </template>
    <ErrorNotice v-if="data.error.value" :error="data.error.value" />

    <section v-if="data.activity.value" class="hooks__summary" aria-label="Últimas 24 horas">
      <div class="hooks__card">
        <span class="hooks__label">Entregas válidas (24 h)</span>
        <span class="hooks__value">{{ valid }}</span>
        <ul class="hooks__list">
          <li v-for="o in data.activity.value.outcomes" :key="o.outcome">
            <StatusTag v-bind="outcome(o.outcome)" />
            <span class="hooks__count">{{ o.count }}</span>
          </li>
        </ul>
      </div>
      <div v-for="reason in REASONS" :key="reason" class="hooks__card">
        <span class="hooks__label">{{ REASON_LABELS[reason] }} (24 h)</span>
        <span class="hooks__value" :class="{ 'hooks__value--alert': reason === 'SIGNATURE' && reasonTotals[reason] > 0 }">
          {{ reasonTotals[reason] }}
        </span>
      </div>
    </section>

    <Panel title="Rechazos y duplicados por hora" flush>
      <p class="hooks__hint">
        Solo se guardan recuentos, nunca el cuerpo, las cabeceras ni la IP. Muchas firmas no válidas indican un secreto mal configurado o
        alguien probando el endpoint.
      </p>
      <DataTable
        caption="Rechazos y duplicados de las últimas 24 horas por hora"
        caption-hidden
        bare
        :columns="hourColumns"
        :rows="hours"
        :row-key="(r: HourRow) => r.hour"
        :loading="data.loading.value"
        empty-text="Ningún rechazo ni duplicado en las últimas 24 horas."
      />
    </Panel>

    <Panel title="Últimas entregas válidas" flush>
      <DataTable
        caption="Últimas 200 entregas aceptadas"
        caption-hidden
        bare
        :columns="deliveryColumns"
        :rows="data.activity.value?.recent ?? []"
        :row-key="(d: Delivery) => d.delivery_id"
        :loading="data.loading.value"
        empty-text="Aún no ha llegado ninguna entrega de GitHub."
      >
        <template #cell-event="{ row }">{{ visibleText(row.event, 32) }}</template>
        <template #cell-outcome="{ row }">
          <StatusTag v-bind="outcome(row.outcome)" />
        </template>
      </DataTable>
    </Panel>
  </PageLayout>
</template>

<style scoped>
.hooks__refresh {
  height: 30px;
}

.hooks__summary {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: var(--space-4);
}

.hooks__card {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding: var(--space-4) var(--space-5);
  border: var(--border);
  border-radius: var(--radius);
  background: var(--surface);
  box-shadow: var(--shadow-sm);
}

.hooks__label {
  color: var(--text-2);
  font-size: var(--fs-sm);
}

.hooks__value {
  font-family: var(--font-mono);
  font-size: var(--fs-2xl);
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  line-height: 1.2;
}

.hooks__value--alert {
  color: var(--danger);
}

.hooks__list {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2) var(--space-4);
  margin: 0;
  padding: 0;
  list-style: none;
}

.hooks__list li {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
}

.hooks__count {
  font-family: var(--font-mono);
  font-size: var(--fs-xs);
  font-weight: 600;
}

.hooks__hint {
  padding: var(--space-3) var(--space-5);
  border-bottom: var(--border);
  color: var(--text-3);
  font-size: var(--fs-xs);
}
</style>
