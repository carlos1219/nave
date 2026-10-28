<script setup lang="ts">
import { computed, onMounted } from "vue";
import { RouterLink } from "vue-router";

import { OpenFindingsSummary, SEVERITIES, severitySegments, toneOf, totalCount } from "@/entities/finding";
import { useOrganizationStats } from "@/entities/organization";
import { isUuid, visibleText } from "@/shared/lib";
import { ErrorNotice, Panel, SeverityBadge, StackedBar, TrendChart } from "@/shared/ui";
import { OrganizationShell } from "@/widgets/organization-shell";

import { SERIES, formatDays, kpis, maxTotal, trendPoints } from "../model/view";

const props = defineProps<{ organizationId: string }>();
const data = useOrganizationStats(() => props.organizationId);
onMounted(() => void data.reload());

const stats = computed(() => data.stats.value);
const severityRows = computed(() => {
  const open = stats.value?.open;
  if (open === undefined) return [];
  const max = Math.max(1, ...SEVERITIES.map((s) => open[toneOf(s)]));
  return SEVERITIES.map((s) => ({ severity: s, count: open[toneOf(s)], max }));
});
const scannerMax = computed(() => maxTotal(stats.value?.by_scanner ?? []));
const repoMax = computed(() => maxTotal(stats.value?.by_repository ?? []));
const ruleMax = computed(() => Math.max(1, ...(stats.value?.top_rules ?? []).map((r) => r.open)));
</script>

<template>
  <OrganizationShell :organization-id="organizationId" section="Estadísticas">
    <ErrorNotice v-if="data.error.value" :error="data.error.value" />
    <p v-if="!stats && data.loading.value" class="stats__loading">Cargando…</p>
    <template v-if="stats">
      <dl class="stats__kpis">
        <div v-for="kpi in kpis(stats)" :key="kpi.label" class="stats__kpi">
          <dt class="stats__kpi-label">{{ kpi.label }}</dt>
          <dd class="stats__kpi-value">{{ kpi.value }}</dd>
          <dd class="stats__kpi-detail">{{ kpi.detail }}</dd>
        </div>
      </dl>

      <Panel title="Evolución de las vulnerabilidades abiertas">
        <template #aside><span>Últimos 30 días, rama por defecto</span></template>
        <TrendChart :points="trendPoints(stats)" :series="SERIES" label="Vulnerabilidades abiertas por día y severidad" />
      </Panel>

      <div class="stats__grid">
        <Panel title="Por severidad">
          <ul class="stats__bars">
            <li v-for="row in severityRows" :key="row.severity" class="stats__bar-row">
              <SeverityBadge :severity="row.severity" />
              <StackedBar
                :segments="[{ key: toneOf(row.severity), value: row.count }]"
                :max="row.max"
                :label="`${row.severity}: ${row.count}`"
              />
              <span class="stats__count">{{ row.count }}</span>
            </li>
          </ul>
        </Panel>
        <Panel title="Por escáner">
          <p v-if="stats.by_scanner.length === 0" class="stats__empty">No hay vulnerabilidades abiertas.</p>
          <ul v-else class="stats__bars">
            <li v-for="row in stats.by_scanner" :key="row.scanner" class="stats__bar-row stats__bar-row--wide">
              <span class="stats__name">{{ visibleText(row.scanner, 32) }}</span>
              <StackedBar :segments="severitySegments(row.open)" :max="scannerMax" :label="`${row.scanner}: ${totalCount(row.open)}`" />
              <span class="stats__count">{{ totalCount(row.open) }}</span>
            </li>
          </ul>
        </Panel>
      </div>

      <Panel title="Por repositorio" flush>
        <p v-if="stats.by_repository.length === 0" class="stats__empty stats__empty--padded">No hay vulnerabilidades abiertas.</p>
        <div v-else class="stats__scroll">
          <table class="stats__table">
            <caption class="visually-hidden">Vulnerabilidades abiertas por repositorio</caption>
            <thead>
              <tr>
                <th scope="col">Repositorio</th>
                <th scope="col" class="stats__bar-col">Distribución</th>
                <th scope="col">Por severidad</th>
                <th scope="col" class="stats__num">Total</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in stats.by_repository" :key="row.repository.id">
                <td class="stats__mono">{{ visibleText(row.repository.owner, 80) }}/{{ visibleText(row.repository.name, 100) }}</td>
                <td class="stats__bar-col">
                  <StackedBar :segments="severitySegments(row.open)" :max="repoMax" :label="`${row.repository.name}: ${totalCount(row.open)}`" />
                </td>
                <td><OpenFindingsSummary :counts="row.open" /></td>
                <td class="stats__num stats__mono">{{ totalCount(row.open) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </Panel>

      <div class="stats__grid">
        <Panel title="Reglas que más saltan" flush>
          <template #aside>
            <RouterLink v-if="isUuid(organizationId)" :to="{ name: 'organization-rules', params: { organizationId } }">Ver todas</RouterLink>
          </template>
          <p v-if="stats.top_rules.length === 0" class="stats__empty stats__empty--padded">Ninguna regla ha producido hallazgos.</p>
          <div v-else class="stats__scroll">
            <table class="stats__table">
              <caption class="visually-hidden">Reglas con más vulnerabilidades abiertas</caption>
              <thead>
                <tr><th scope="col">Regla</th><th scope="col">Severidad</th><th scope="col" class="stats__bar-col">Abiertas</th><th scope="col" class="stats__num">Total</th></tr>
              </thead>
              <tbody>
                <tr v-for="rule in stats.top_rules" :key="`${rule.scanner}:${rule.rule_id}`">
                  <td class="stats__rule">{{ visibleText(rule.rule_id, 200) }}</td>
                  <td><SeverityBadge :severity="rule.max_severity" /></td>
                  <td class="stats__bar-col">
                    <span class="stats__inline-bar">
                      <StackedBar :segments="[{ key: toneOf(rule.max_severity), value: rule.open }]" :max="ruleMax" :label="`${rule.open} abiertas`" />
                      <span class="stats__count">{{ rule.open }}</span>
                    </span>
                  </td>
                  <td class="stats__num stats__mono">{{ rule.total }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </Panel>

        <div class="stats__column">
          <Panel title="Plazos de corrección" flush>
            <p v-if="stats.sla.length === 0" class="stats__empty stats__empty--padded">La política de la organización no define plazos.</p>
            <div v-else class="stats__scroll">
              <table class="stats__table">
                <caption class="visually-hidden">Hallazgos abiertos fuera de plazo</caption>
                <thead><tr><th scope="col">Severidad</th><th scope="col" class="stats__num">Plazo</th><th scope="col" class="stats__num">Fuera de plazo</th></tr></thead>
                <tbody>
                  <tr v-for="sla in stats.sla" :key="sla.severity">
                    <td><SeverityBadge :severity="sla.severity" /></td>
                    <td class="stats__num stats__mono">{{ sla.days }} días</td>
                    <td class="stats__num stats__mono" :class="{ 'stats__overdue': sla.overdue > 0 }">{{ sla.overdue }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </Panel>
          <Panel title="Tiempo medio de corrección" flush>
            <p v-if="stats.fix_times.length === 0" class="stats__empty stats__empty--padded">Aún no hay hallazgos corregidos.</p>
            <div v-else class="stats__scroll">
              <table class="stats__table">
                <caption class="visually-hidden">Tiempo medio de corrección por severidad</caption>
                <thead><tr><th scope="col">Severidad</th><th scope="col" class="stats__num">Corregidos</th><th scope="col" class="stats__num">Media</th></tr></thead>
                <tbody>
                  <tr v-for="fix in stats.fix_times" :key="fix.severity">
                    <td><SeverityBadge :severity="fix.severity" /></td>
                    <td class="stats__num stats__mono">{{ fix.fixed }}</td>
                    <td class="stats__num stats__mono">{{ formatDays(fix.mean_days) }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </Panel>
        </div>
      </div>
    </template>
  </OrganizationShell>
</template>

<style scoped>
.stats__scroll {
  overflow-x: auto;
}

.stats__loading,
.stats__empty {
  color: var(--text-3);
  font-size: var(--fs-sm);
}

.stats__empty--padded {
  padding: var(--space-4) var(--space-5);
}

.stats__kpis {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: var(--space-4);
}

.stats__kpi {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
  padding: var(--space-4) var(--space-5);
  border: var(--border);
  border-radius: var(--radius);
  background: var(--surface);
  box-shadow: var(--shadow-sm);
}

.stats__kpi-label {
  color: var(--text-2);
  font-size: var(--fs-sm);
}

.stats__kpi-value {
  font-family: var(--font-mono);
  font-size: var(--fs-2xl);
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  line-height: 1.2;
}

.stats__kpi-detail {
  color: var(--text-3);
  font-size: var(--fs-xs);
}

.stats__grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(360px, 1fr));
  align-items: start;
  gap: var(--space-5);
}

.stats__column {
  display: flex;
  flex-direction: column;
  gap: var(--space-5);
  min-width: 0;
}

.stats__bars {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  margin: 0;
  padding: 0;
  list-style: none;
}

.stats__bar-row {
  display: grid;
  grid-template-columns: 90px minmax(0, 1fr) 48px;
  align-items: center;
  gap: var(--space-4);
}

.stats__bar-row--wide {
  grid-template-columns: 120px minmax(0, 1fr) 48px;
}

.stats__name {
  overflow: hidden;
  font-family: var(--font-mono);
  font-size: var(--fs-sm);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.stats__count {
  font-family: var(--font-mono);
  font-size: var(--fs-sm);
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  text-align: right;
}

.stats__table {
  width: 100%;
  border-collapse: separate;
  border-spacing: 0;
}

.stats__table th:first-child,
.stats__table td:first-child {
  padding-left: var(--space-5);
}

.stats__table th:last-child,
.stats__table td:last-child {
  padding-right: var(--space-5);
}

.stats__bar-col {
  width: 30%;
  min-width: 120px;
}

.stats__inline-bar {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 32px;
  align-items: center;
  gap: var(--space-3);
}

.stats__num {
  text-align: right;
}

.stats__mono {
  font-family: var(--font-mono);
  font-size: var(--fs-xs);
  font-variant-numeric: tabular-nums;
}

.stats__rule {
  max-width: 32ch;
  overflow: hidden;
  font-family: var(--font-mono);
  font-size: var(--fs-xs);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.stats__overdue {
  color: var(--danger);
  font-weight: 700;
}

@media (max-width: 640px) {
  .stats__grid {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
