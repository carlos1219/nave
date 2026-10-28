<script setup lang="ts">
import { computed } from "vue";

import type { Scan } from "@/shared/api";
import { formatDuration, formatTimestamp, visibleText } from "@/shared/lib";
import { Panel, SeverityBadge } from "@/shared/ui";
import type { SeverityLevel } from "@/shared/ui";

const props = defineProps<{ scan: Scan }>();

const LEVELS = [
  ["critical", "CRITICAL"],
  ["high", "HIGH"],
  ["medium", "MEDIUM"],
  ["low", "LOW"],
  ["info", "INFO"],
] as const satisfies readonly (readonly [string, SeverityLevel])[];

const rows = computed(() => {
  const summary = props.scan.summary;
  if (summary === null) return [];
  return [
    { key: "new", label: "Nuevos", counts: LEVELS.map(([key]) => summary.new[key]) },
    { key: "open", label: "Abiertos", counts: LEVELS.map(([key]) => summary.open[key]) },
  ];
});
const duration = computed(() => formatDuration(props.scan.started_at, props.scan.finished_at));
</script>

<template>
  <Panel title="Resumen">
    <dl class="scan-overview__facts">
      <div class="scan-overview__fact">
        <dt>Referencia</dt>
        <dd class="scan-overview__mono">{{ visibleText(scan.ref, 200) }}</dd>
      </div>
      <div class="scan-overview__fact scan-overview__fact--wide">
        <dt>Commit</dt>
        <dd class="scan-overview__mono">{{ visibleText(scan.commit, 40) }}</dd>
      </div>
      <div class="scan-overview__fact">
        <dt>Origen</dt>
        <dd class="scan-overview__mono">
          {{ visibleText(scan.trigger, 32) }}<template v-if="scan.pull_request_number !== null">, PR {{ scan.pull_request_number }}</template>
        </dd>
      </div>
      <div class="scan-overview__fact">
        <dt>En cola</dt>
        <dd class="scan-overview__mono">{{ formatTimestamp(scan.queued_at) }}</dd>
      </div>
      <div class="scan-overview__fact">
        <dt>Inicio</dt>
        <dd class="scan-overview__mono">{{ scan.started_at ? formatTimestamp(scan.started_at) : "—" }}</dd>
      </div>
      <div class="scan-overview__fact">
        <dt>Fin</dt>
        <dd class="scan-overview__mono">{{ scan.finished_at ? formatTimestamp(scan.finished_at) : "—" }}</dd>
      </div>
      <div class="scan-overview__fact">
        <dt>Duración</dt>
        <dd class="scan-overview__mono">{{ duration }}</dd>
      </div>
    </dl>
    <p v-if="scan.status_reason" class="scan-overview__reason">
      <span class="scan-overview__reason-code">{{ visibleText(scan.status_reason.code, 64) }}</span>
      {{ visibleText(scan.status_reason.detail, 300) }}
    </p>
    <div v-if="scan.summary" class="scan-overview__matrix-wrap">
      <table class="scan-overview__matrix">
        <caption class="visually-hidden">Hallazgos por severidad</caption>
        <thead>
          <tr>
            <td class="scan-overview__corner" />
            <th v-for="[key, level] in LEVELS" :key="key" scope="col" class="scan-overview__level">
              <SeverityBadge :severity="level" />
            </th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in rows" :key="row.key">
            <th scope="row" class="scan-overview__row-label">{{ row.label }}</th>
            <td
              v-for="(count, index) in row.counts"
              :key="index"
              class="scan-overview__count"
              :class="{ 'scan-overview__count--zero': count === 0 }"
            >
              {{ count }}
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    <p v-else class="scan-overview__pending">Los recuentos aparecen cuando el escaneo termina de procesarse.</p>
    <p v-if="scan.summary?.truncated" class="scan-overview__warn">
      Resultado truncado: hay más hallazgos de los que se cuentan aquí.
    </p>
  </Panel>
</template>

<style scoped>
.scan-overview__facts {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
  gap: var(--space-4) var(--space-5);
}

.scan-overview__fact {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
  min-width: 0;
}

.scan-overview__fact--wide {
  grid-column: span 2;
}

.scan-overview__mono {
  font-family: var(--font-mono);
  font-size: var(--fs-sm);
  font-variant-numeric: tabular-nums;
  overflow-wrap: anywhere;
}

.scan-overview__reason {
  padding: var(--space-3) var(--space-4);
  border-left: 3px solid var(--danger);
  border-radius: var(--radius-sm);
  background: var(--danger-bg);
  color: var(--danger);
  font-size: var(--fs-sm);
}

.scan-overview__reason-code {
  margin-right: var(--space-3);
  font-family: var(--font-mono);
  font-weight: 700;
}

.scan-overview__matrix-wrap {
  overflow-x: auto;
  border: var(--border);
  border-radius: var(--radius-sm);
}

.scan-overview__matrix {
  width: 100%;
  border-collapse: separate;
  border-spacing: 0;
}

.scan-overview__corner,
.scan-overview__level {
  background: var(--surface-2);
}

.scan-overview__level {
  text-align: right;
}

.scan-overview__row-label {
  width: 120px;
  background: var(--surface);
  color: var(--text-2);
  font-size: var(--fs-sm);
  font-weight: 600;
  white-space: nowrap;
}

/* La cifra es el dato principal de la página: grande, monoespaciada y alineada a la derecha. */
.scan-overview__count {
  color: var(--text);
  font-family: var(--font-mono);
  font-size: var(--fs-xl);
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  text-align: right;
}

.scan-overview__count + .scan-overview__count,
.scan-overview__level + .scan-overview__level {
  border-left: var(--border);
}

.scan-overview__count--zero {
  color: var(--text-3);
  font-weight: 400;
}

.scan-overview__pending {
  color: var(--text-3);
  font-size: var(--fs-sm);
}

.scan-overview__warn {
  padding: var(--space-3) var(--space-4);
  border-left: 3px solid var(--warn);
  border-radius: var(--radius-sm);
  background: var(--warn-bg);
  color: var(--warn);
  font-size: var(--fs-sm);
  font-weight: 600;
}

@media (max-width: 640px) {
  .scan-overview__fact--wide {
    grid-column: auto;
  }
}
</style>
