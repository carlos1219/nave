<script setup lang="ts">
import type { Severity, TriageState } from "@/shared/api";

import { FILTER_SEVERITIES, FILTER_STATES } from "../model/filter";
import type { FindingsFilterState } from "../model/filter";

defineProps<{ filter: FindingsFilterState; scanners: readonly string[]; active: boolean }>();
const emit = defineEmits<{
  severity: [value: Severity];
  state: [value: TriageState];
  scanner: [value: string];
  onlyNew: [value: boolean];
  reset: [];
}>();

function onOnlyNew(event: Event): void {
  emit("onlyNew", (event.target as HTMLInputElement).checked);
}
</script>

<template>
  <form class="findings-filter" aria-label="Filtros de hallazgos" @submit.prevent>
    <fieldset class="findings-filter__group">
      <legend class="findings-filter__legend">Severidad</legend>
      <div class="findings-filter__chips">
        <label
          v-for="value in FILTER_SEVERITIES"
          :key="value"
          class="findings-filter__chip"
          :class="`findings-filter__chip--${value.toLowerCase()}`"
        >
          <input type="checkbox" class="findings-filter__box" :checked="filter.severity.includes(value)" @change="emit('severity', value)">
          {{ value }}
        </label>
      </div>
    </fieldset>
    <fieldset class="findings-filter__group">
      <legend class="findings-filter__legend">Estado de triage</legend>
      <div class="findings-filter__chips">
        <label v-for="value in FILTER_STATES" :key="value" class="findings-filter__chip">
          <input type="checkbox" class="findings-filter__box" :checked="filter.state.includes(value)" @change="emit('state', value)">
          {{ value }}
        </label>
      </div>
    </fieldset>
    <fieldset v-if="scanners.length > 0" class="findings-filter__group">
      <legend class="findings-filter__legend">Escáner</legend>
      <div class="findings-filter__chips">
        <label v-for="value in scanners" :key="value" class="findings-filter__chip">
          <input type="checkbox" class="findings-filter__box" :checked="filter.scanner.includes(value)" @change="emit('scanner', value)">
          {{ value }}
        </label>
      </div>
    </fieldset>
    <div class="findings-filter__end">
      <label class="findings-filter__chip">
        <input type="checkbox" class="findings-filter__box" :checked="filter.onlyNew" @change="onOnlyNew">
        Solo nuevos
      </label>
      <button type="button" class="findings-filter__reset" :disabled="!active" @click="emit('reset')">
        Limpiar filtros
      </button>
    </div>
  </form>
</template>

<style scoped>
.findings-filter {
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

.findings-filter__group {
  min-width: 0;
  margin: 0;
  padding: 0;
  border: 0;
}

.findings-filter__legend {
  margin-bottom: var(--space-2);
  padding: 0;
  color: var(--text-3);
  font-size: var(--fs-xs);
  font-weight: 500;
}

.findings-filter__chips {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
}

/* Cada opción es una casilla real dentro de su etiqueta: el aspecto de ficha no cambia la semántica. */
.findings-filter__chip {
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

.findings-filter__chip:hover {
  border-color: var(--text-3);
}

.findings-filter__chip:has(:checked) {
  border-color: var(--accent);
  background: var(--accent-soft);
  color: var(--accent);
}

.findings-filter__chip--critical:has(:checked) { border-color: var(--sev-critical-dot); background: var(--sev-critical-bg); color: var(--sev-critical); }
.findings-filter__chip--high:has(:checked) { border-color: var(--sev-high-dot); background: var(--sev-high-bg); color: var(--sev-high); }
.findings-filter__chip--medium:has(:checked) { border-color: var(--sev-medium-dot); background: var(--sev-medium-bg); color: var(--sev-medium); }
.findings-filter__chip--low:has(:checked) { border-color: var(--sev-low-dot); background: var(--sev-low-bg); color: var(--sev-low); }
.findings-filter__chip--info:has(:checked) { border-color: var(--sev-info-dot); background: var(--sev-info-bg); color: var(--sev-info); }

.findings-filter__chip:has(:focus-visible) {
  outline: 2px solid var(--focus);
  outline-offset: 2px;
}

.findings-filter__box:focus-visible {
  outline: 0;
}

.findings-filter__end {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  margin-left: auto;
}

.findings-filter__reset {
  height: 28px;
}
</style>
