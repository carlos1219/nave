<script setup lang="ts" generic="Row extends object">
import { visibleText } from "@/shared/lib";

import type { Column, SortState, Tone } from "./types";

const props = defineProps<{
  columns: readonly Column<Row>[];
  rows: readonly Row[];
  rowKey: (row: Row) => string;
  /** Nombre accesible de la tabla (se anuncia a lectores de pantalla). */
  caption: string;
  /** El título ya está a la vista en otro sitio: se deja solo para lectores de pantalla. */
  captionHidden?: boolean;
  /** Sin marco propio, para ir dentro de un `Panel`. */
  bare?: boolean;
  sort?: SortState | null;
  loading?: boolean;
  emptyText?: string;
  tone?: (row: Row) => Tone | undefined;
}>();

const emit = defineEmits<{ sort: [key: string] }>();

function cellText(row: Row, column: Column<Row>): string {
  if (column.text !== undefined) return visibleText(column.text(row), 500);
  const value = (row as Record<string, unknown>)[column.key];
  if (typeof value === "string") return visibleText(value, 500);
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return "";
}

function ariaSort(column: Column<Row>): "ascending" | "descending" | "none" | undefined {
  if (column.sortable !== true) return undefined;
  if (props.sort?.key !== column.key) return "none";
  return props.sort.direction === "asc" ? "ascending" : "descending";
}

function direction(column: Column<Row>): string {
  if (props.sort?.key !== column.key) return "";
  return props.sort.direction === "asc" ? "ASC" : "DESC";
}
</script>

<template>
  <div class="data-table" :class="{ 'data-table--bare': bare === true }" :aria-busy="loading === true">
    <table class="data-table__table">
      <caption class="data-table__caption" :class="{ 'visually-hidden': captionHidden === true }">{{ caption }}</caption>
      <thead>
        <tr>
          <th
            v-for="column in columns"
            :key="column.key"
            scope="col"
            class="data-table__head"
            :class="{ 'data-table__head--numeric': column.numeric === true }"
            :aria-sort="ariaSort(column)"
          >
            <button
              v-if="column.sortable === true"
              type="button"
              class="data-table__sort"
              @click="emit('sort', column.key)"
            >
              {{ column.header }}
              <span class="data-table__direction">{{ direction(column) }}</span>
            </button>
            <template v-else>{{ column.header }}</template>
          </th>
        </tr>
      </thead>
      <tbody>
        <tr v-if="loading === true && rows.length === 0">
          <td class="data-table__status" :colspan="columns.length">Cargando…</td>
        </tr>
        <tr v-else-if="rows.length === 0">
          <td class="data-table__status" :colspan="columns.length">{{ emptyText ?? "Sin resultados" }}</td>
        </tr>
        <tr
          v-for="row in rows"
          :key="rowKey(row)"
          class="data-table__row"
          :data-tone="tone?.(row)"
        >
          <td
            v-for="column in columns"
            :key="column.key"
            class="data-table__cell"
            :class="{
              'data-table__cell--numeric': column.numeric === true,
              'data-table__cell--mono': column.mono === true || column.numeric === true,
            }"
          >
            <slot :name="`cell-${column.key}`" :row="row">{{ cellText(row, column) }}</slot>
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>

<style scoped>
/* El contenedor desplaza en horizontal: una tabla ancha nunca rompe la página en móvil. */
.data-table {
  overflow-x: auto;
  border: var(--border);
  border-radius: var(--radius);
  background: var(--surface);
  box-shadow: var(--shadow-sm);
}

.data-table--bare {
  border: 0;
  border-radius: 0;
  box-shadow: none;
}

.data-table__table {
  width: 100%;
  border-collapse: separate;
  border-spacing: 0;
}

.data-table__caption {
  padding: var(--space-3) var(--space-5);
  border-bottom: var(--border);
  font-size: var(--fs-md);
  font-weight: 600;
}

.data-table__head {
  height: 34px;
  white-space: nowrap;
}

.data-table__head:first-child,
.data-table__cell:first-child {
  padding-left: var(--space-5);
}

.data-table__head:last-child,
.data-table__cell:last-child {
  padding-right: var(--space-5);
}

.data-table__head--numeric,
.data-table__cell--numeric {
  text-align: right;
}

.data-table__sort {
  width: 100%;
  height: auto;
  padding: 0;
  border: 0;
  background: transparent;
  color: inherit;
  font: inherit;
  text-align: inherit;
}

.data-table__sort:hover:not(:disabled) {
  background: transparent;
  color: var(--text);
}

.data-table__direction {
  margin-left: var(--space-2);
  color: var(--accent);
  font-family: var(--font-mono);
}

.data-table__cell {
  height: 38px;
  max-width: 60ch;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.data-table__cell--mono {
  color: var(--text-2);
  font-family: var(--font-mono);
  font-size: var(--fs-xs);
  font-variant-numeric: tabular-nums;
}

.data-table__status {
  padding: var(--space-7) var(--space-5);
  color: var(--text-3);
  text-align: center;
}

.data-table__row:hover > td {
  background: var(--surface-hover);
}

.data-table__row[data-tone] > :first-child {
  box-shadow: inset 3px 0 0 var(--tone-color, transparent);
}

.data-table__row[data-tone="critical"] { --tone-color: var(--sev-critical-dot); }
.data-table__row[data-tone="high"] { --tone-color: var(--sev-high-dot); }
.data-table__row[data-tone="medium"] { --tone-color: var(--sev-medium-dot); }
.data-table__row[data-tone="low"] { --tone-color: var(--sev-low-dot); }
.data-table__row[data-tone="info"] { --tone-color: var(--sev-info-dot); }
</style>
