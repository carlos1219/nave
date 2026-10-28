<script setup lang="ts">
import { onMounted, watch } from "vue";

import { FindingTitleLink, SEVERITIES, toneOf } from "@/entities/finding";
import { useTriageQueue } from "@/entities/organization";
import { useRepositories } from "@/entities/repository";
import { ExportLinks } from "@/features/export-findings";
import type { FindingRow } from "@/shared/api";
import { visibleText } from "@/shared/lib";
import { DataTable, ErrorNotice, LoadMore, Panel, SeverityBadge } from "@/shared/ui";
import type { Column } from "@/shared/ui";
import { OrganizationShell } from "@/widgets/organization-shell";

import { ageLabel } from "../model/age";
import { useQueueFilter } from "../model/queueFilter";

const props = defineProps<{ organizationId: string }>();
const filter = useQueueFilter();
const queue = useTriageQueue(() => props.organizationId, () => filter.query.value);
const repos = useRepositories(() => props.organizationId);
onMounted(() => void repos.reload());
watch(() => filter.query.value, () => void queue.reset(), { deep: true, immediate: true });

const now = Date.now();
const columns: readonly Column<FindingRow>[] = [
  { key: "severity", header: "Severidad" },
  { key: "title", header: "Hallazgo" },
  { key: "repository", header: "Repositorio", mono: true, text: (r) => r.repository.name },
  { key: "scanner", header: "Escáner", mono: true, text: (r) => r.finding.scanner },
  { key: "age", header: "Abierto desde", numeric: true, text: (r) => ageLabel(r.finding.first_seen_at, now) },
];
</script>

<template>
  <OrganizationShell :organization-id="organizationId" section="Cola de triage">
    <form class="queue-filter" aria-label="Filtros de la cola de triage" @submit.prevent>
      <fieldset class="queue-filter__group">
        <legend class="queue-filter__legend">Severidad</legend>
        <div class="queue-filter__chips">
          <label v-for="value in SEVERITIES" :key="value" class="queue-filter__chip" :class="`queue-filter__chip--${toneOf(value)}`">
            <input type="checkbox" class="queue-filter__box" :checked="filter.state.severity.includes(value)" @change="filter.toggleSeverity(value)">
            {{ value }}
          </label>
        </div>
      </fieldset>
      <label class="queue-filter__group">
        <span class="queue-filter__legend">Repositorio</span>
        <select v-model="filter.state.repositoryId" class="queue-filter__select">
          <option value="">Todos</option>
          <option v-for="repo in repos.repositories.value ?? []" :key="repo.id" :value="repo.id">
            {{ visibleText(repo.owner, 80) }}/{{ visibleText(repo.name, 100) }}
          </option>
        </select>
      </label>
      <button type="button" class="queue-filter__reset" :disabled="!filter.active.value" @click="filter.reset">
        Limpiar filtros
      </button>
    </form>

    <ErrorNotice v-if="queue.error.value" :error="queue.error.value" />
    <Panel title="Hallazgos abiertos sin clasificar" flush>
      <template #aside>
        <span>{{ queue.rows.value.length }}{{ queue.hasMore.value ? "+" : "" }} mostrados</span>
        <ExportLinks :source="{ kind: 'organization', organizationId, view: 'triage', filter: filter.exportFilter.value }" />
      </template>
      <p class="queue__hint">De mayor a menor severidad y, dentro de cada una, el más antiguo primero.</p>
      <DataTable
        caption="Cola de triage de la organización"
        caption-hidden
        bare
        :columns="columns"
        :rows="queue.rows.value"
        :row-key="(r: FindingRow) => r.finding.id"
        :tone="(r: FindingRow) => toneOf(r.finding.severity)"
        :loading="queue.loading.value"
        :empty-text="filter.active.value ? 'Ningún hallazgo abierto coincide con el filtro.' : 'No quedan hallazgos abiertos por clasificar.'"
      >
        <template #cell-severity="{ row }">
          <SeverityBadge :severity="row.finding.severity" />
        </template>
        <template #cell-title="{ row }">
          <FindingTitleLink :finding="row.finding" />
        </template>
      </DataTable>
    </Panel>
    <LoadMore :has-more="queue.hasMore.value" :loading="queue.loadingMore.value" :capped="queue.capped.value" @more="queue.loadMore" />
  </OrganizationShell>
</template>

<style scoped>
.queue-filter {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-end;
  gap: var(--space-4) var(--space-6);
  padding: var(--space-4) var(--space-5);
  border: var(--border);
  border-radius: var(--radius);
  background: var(--surface);
  box-shadow: var(--shadow-sm);
}

.queue-filter__group {
  display: flex;
  flex-direction: column;
  min-width: 0;
  margin: 0;
  padding: 0;
  border: 0;
}

.queue-filter__legend {
  margin-bottom: var(--space-2);
  padding: 0;
  color: var(--text-3);
  font-size: var(--fs-xs);
  font-weight: 500;
}

.queue-filter__chips {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
}

.queue-filter__chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 28px;
  padding: 0 var(--space-3);
  border: 1px solid var(--line-strong);
  border-radius: var(--radius-sm);
  background: var(--surface);
  color: var(--text-2);
  font-family: var(--font-mono);
  font-size: 11px;
  font-weight: 600;
  white-space: nowrap;
  cursor: pointer;
}

.queue-filter__chip:hover {
  border-color: var(--text-3);
}

.queue-filter__chip--critical:has(:checked) { border-color: var(--sev-critical-dot); background: var(--sev-critical-bg); color: var(--sev-critical); }
.queue-filter__chip--high:has(:checked) { border-color: var(--sev-high-dot); background: var(--sev-high-bg); color: var(--sev-high); }
.queue-filter__chip--medium:has(:checked) { border-color: var(--sev-medium-dot); background: var(--sev-medium-bg); color: var(--sev-medium); }
.queue-filter__chip--low:has(:checked) { border-color: var(--sev-low-dot); background: var(--sev-low-bg); color: var(--sev-low); }
.queue-filter__chip--info:has(:checked) { border-color: var(--sev-info-dot); background: var(--sev-info-bg); color: var(--sev-info); }

.queue-filter__chip:has(:focus-visible) {
  outline: 2px solid var(--focus);
  outline-offset: 2px;
}

.queue-filter__box:focus-visible {
  outline: 0;
}

.queue-filter__select {
  min-width: 220px;
  height: 28px;
}

.queue-filter__reset {
  height: 28px;
  margin-left: auto;
}

.queue__hint {
  padding: var(--space-3) var(--space-5);
  border-bottom: var(--border);
  color: var(--text-3);
  font-size: var(--fs-xs);
}
</style>
