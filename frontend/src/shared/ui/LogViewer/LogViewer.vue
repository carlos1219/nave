<script setup lang="ts">
import { computed, toRef } from "vue";

import { formatTimestamp, visibleText } from "@/shared/lib";

import type { LogLine } from "./types";
import { useFollowTail } from "./useFollowTail";

/** Tope de líneas pintadas: el resto sigue en el servidor, no en el DOM. */
const MAX_LINES = 5000;
const MAX_MESSAGE = 2000;

const props = defineProps<{ lines: readonly LogLine[]; label: string }>();

const shown = computed(() => props.lines.slice(-MAX_LINES));
const omitted = computed(() => props.lines.length - shown.value.length);
const { onScroll } = useFollowTail(toRef(() => shown.value.length), "scroller");
</script>

<template>
  <div
    ref="scroller"
    class="log-viewer"
    role="log"
    tabindex="0"
    :aria-label="label"
    @scroll="onScroll"
  >
    <p v-if="omitted > 0" class="log-viewer__omitted">… {{ omitted }} líneas anteriores no se muestran</p>
    <p v-if="shown.length === 0" class="log-viewer__empty">Sin registros todavía.</p>
    <pre v-for="line in shown" :key="line.seq" class="log-viewer__line" :class="`log-viewer__line--${line.level.toLowerCase()}`"><span class="log-viewer__time">{{ formatTimestamp(line.at) }}</span> <span class="log-viewer__level">{{ line.level.padEnd(7) }}</span> {{ visibleText(line.message, MAX_MESSAGE) }}</pre>
  </div>
</template>

<style scoped>
.log-viewer {
  max-height: 520px;
  min-height: 120px;
  overflow: auto;
  padding: var(--space-3) 0;
  background: var(--log-bg);
  color: var(--log-text);
  font-family: var(--font-mono);
  font-size: var(--fs-xs);
  line-height: 1.75;
  font-variant-numeric: tabular-nums;
}

.log-viewer:focus-visible {
  outline-offset: -2px;
}

.log-viewer__line {
  margin: 0;
  padding: 0 var(--space-5);
  font-size: inherit;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.log-viewer__line:hover {
  background: var(--log-line);
}

.log-viewer__time {
  color: var(--log-muted);
}

.log-viewer__level {
  color: var(--log-muted);
  font-weight: 600;
}

.log-viewer__line--warning .log-viewer__level {
  color: var(--log-warn);
}

.log-viewer__line--error {
  background: var(--log-error-bg);
  color: var(--log-error);
}

.log-viewer__line--error .log-viewer__level {
  color: var(--log-error);
}

.log-viewer__omitted,
.log-viewer__empty {
  margin: 0;
  padding: var(--space-3) var(--space-5);
  color: var(--log-muted);
}
</style>
