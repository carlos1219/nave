<script setup lang="ts">
import { computed } from "vue";

import type { SeverityLevel } from "./types";

const props = defineProps<{ severity: SeverityLevel }>();

// La etiqueta de texto es la fuente de verdad; el color solo la refuerza (§13).
const modifier = computed(() => props.severity.toLowerCase());
</script>

<template>
  <span class="severity-badge" :class="`severity-badge--${modifier}`">{{ severity }}</span>
</template>

<style scoped>
.severity-badge {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-width: 84px;
  padding: 0 var(--space-3) 0 6px;
  border-radius: var(--radius-sm);
  background: var(--badge-bg);
  color: var(--badge-fg);
  font-family: var(--font-mono);
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.04em;
  line-height: 20px;
  white-space: nowrap;
}

.severity-badge::before {
  content: "";
  flex-shrink: 0;
  width: 3px;
  height: 12px;
  border-radius: var(--radius-sm);
  background: var(--badge-dot);
}

.severity-badge--critical { --badge-bg: var(--sev-critical-bg); --badge-fg: var(--sev-critical); --badge-dot: var(--sev-critical-dot); }
.severity-badge--high { --badge-bg: var(--sev-high-bg); --badge-fg: var(--sev-high); --badge-dot: var(--sev-high-dot); }
.severity-badge--medium { --badge-bg: var(--sev-medium-bg); --badge-fg: var(--sev-medium); --badge-dot: var(--sev-medium-dot); }
.severity-badge--low { --badge-bg: var(--sev-low-bg); --badge-fg: var(--sev-low); --badge-dot: var(--sev-low-dot); }
.severity-badge--info { --badge-bg: var(--sev-info-bg); --badge-fg: var(--sev-info); --badge-dot: var(--sev-info-dot); }
</style>
