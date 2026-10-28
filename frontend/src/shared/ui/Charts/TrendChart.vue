<script setup lang="ts">
import { computed } from "vue";

import { niceMax, stackedAreas, yTicks } from "@/shared/lib";
import type { Box } from "@/shared/lib";

import type { Tone } from "../DataTable/types";

const props = defineProps<{
  points: readonly { label: string; values: Readonly<Record<Tone, number>> }[];
  /** De abajo arriba; también es el orden de la leyenda. */
  series: readonly { key: Tone; label: string }[];
  label: string;
}>();

// Lienzo ancho: al escalar a la anchura del panel, el texto de los ejes queda cerca de 11 px.
const BOX: Box = { width: 1200, height: 280, left: 44, right: 12, top: 12, bottom: 30 };

const totals = computed(() => props.points.map((p) => props.series.reduce((sum, s) => sum + p.values[s.key], 0)));
const max = computed(() => niceMax(Math.max(0, ...totals.value)));
const ticks = computed(() => yTicks(max.value, BOX));
const bands = computed(() =>
  stackedAreas(
    props.points.map((p) => p.values),
    props.series.map((s) => s.key),
    BOX,
    max.value,
  ),
);
const xLabels = computed(() => {
  const n = props.points.length;
  if (n === 0) return [];
  const plot = BOX.width - BOX.left - BOX.right;
  const at = (i: number) => ({ x: BOX.left + (n > 1 ? (i * plot) / (n - 1) : 0), text: props.points[i]?.label ?? "" });
  return n > 2 ? [at(0), at(Math.floor((n - 1) / 2)), at(n - 1)] : [at(0), at(n - 1)];
});
const latest = computed(() => totals.value.at(-1) ?? 0);
</script>

<template>
  <figure class="trend-chart">
    <div class="trend-chart__scroll">
      <svg class="trend-chart__svg" :viewBox="`0 0 ${BOX.width} ${BOX.height}`" role="img" :aria-label="`${label}. Último valor: ${latest}.`">
        <g class="trend-chart__grid">
          <line v-for="tick in ticks" :key="tick.value" :x1="BOX.left" :x2="BOX.width - BOX.right" :y1="tick.y" :y2="tick.y" />
        </g>
        <g class="trend-chart__axis">
          <text v-for="tick in ticks" :key="tick.value" :x="BOX.left - 8" :y="tick.y + 4" text-anchor="end">{{ tick.value }}</text>
          <text
            v-for="(item, index) in xLabels"
            :key="index"
            :x="item.x"
            :y="BOX.height - 8"
            :text-anchor="index === 0 ? 'start' : index === xLabels.length - 1 ? 'end' : 'middle'"
          >
            {{ item.text }}
          </text>
        </g>
        <path v-for="band in bands" :key="band.key" class="trend-chart__band" :class="`trend-chart__band--${band.key}`" :d="band.path" />
      </svg>
    </div>
    <figcaption class="trend-chart__legend">
      <span v-for="item in series" :key="item.key" class="trend-chart__key" :class="`trend-chart__key--${item.key}`">{{ item.label }}</span>
    </figcaption>
  </figure>
</template>

<style scoped>
.trend-chart {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  margin: 0;
}

/* En pantallas estrechas la gráfica no encoge por debajo de lo legible: se desplaza. */
.trend-chart__scroll {
  overflow-x: auto;
}

.trend-chart__svg {
  display: block;
  width: 100%;
  min-width: 720px;
  height: auto;
}

.trend-chart__grid line {
  stroke: var(--line);
  stroke-width: 1;
}

.trend-chart__axis text {
  fill: var(--text-3);
  font-family: var(--font-mono);
  font-size: 11px;
  font-variant-numeric: tabular-nums;
}

.trend-chart__band {
  stroke: var(--surface);
  stroke-width: 1;
}

.trend-chart__band--critical { fill: var(--sev-critical-dot); }
.trend-chart__band--high { fill: var(--sev-high-dot); }
.trend-chart__band--medium { fill: var(--sev-medium-dot); }
.trend-chart__band--low { fill: var(--sev-low-dot); }
.trend-chart__band--info { fill: var(--sev-info-dot); }

.trend-chart__legend {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2) var(--space-5);
  color: var(--text-2);
  font-family: var(--font-mono);
  font-size: 11px;
  font-weight: 600;
}

.trend-chart__key {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.trend-chart__key::before {
  content: "";
  width: 10px;
  height: 10px;
  border-radius: var(--radius-sm);
  background: var(--key-color);
}

.trend-chart__key--critical { --key-color: var(--sev-critical-dot); }
.trend-chart__key--high { --key-color: var(--sev-high-dot); }
.trend-chart__key--medium { --key-color: var(--sev-medium-dot); }
.trend-chart__key--low { --key-color: var(--sev-low-dot); }
.trend-chart__key--info { --key-color: var(--sev-info-dot); }
</style>
