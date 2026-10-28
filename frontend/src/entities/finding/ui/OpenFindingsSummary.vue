<script setup lang="ts">
import { computed } from "vue";

import type { SeverityCounts } from "@/shared/api";

import { nonZeroCounts } from "../model/counts";

// Cada cifra lleva su severidad escrita: el color solo la refuerza (§13).
const props = defineProps<{ counts: SeverityCounts }>();
const items = computed(() => nonZeroCounts(props.counts));
</script>

<template>
  <ul v-if="items.length > 0" class="open-findings" aria-label="Vulnerabilidades abiertas por severidad">
    <li
      v-for="item in items"
      :key="item.severity"
      class="open-findings__item"
      :class="`open-findings__item--${item.severity.toLowerCase()}`"
    >
      <span class="open-findings__label">{{ item.severity }}</span> <span class="open-findings__count">{{ item.count }}</span>
    </li>
  </ul>
  <span v-else class="open-findings__none">Sin abiertas</span>
</template>

<style scoped>
.open-findings {
  display: flex;
  flex-wrap: nowrap;
  gap: var(--space-2);
  margin: 0;
  padding: 0;
  list-style: none;
}

.open-findings__item {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 0 var(--space-3) 0 6px;
  border-left: 3px solid var(--item-dot);
  border-radius: var(--radius-sm);
  background: var(--item-bg);
  color: var(--item-fg);
  font-family: var(--font-mono);
  font-size: 11px;
  line-height: 20px;
  white-space: nowrap;
}

.open-findings__label {
  font-weight: 600;
  letter-spacing: 0.04em;
}

.open-findings__count {
  font-weight: 700;
  font-variant-numeric: tabular-nums;
}

.open-findings__item--critical { --item-bg: var(--sev-critical-bg); --item-fg: var(--sev-critical); --item-dot: var(--sev-critical-dot); }
.open-findings__item--high { --item-bg: var(--sev-high-bg); --item-fg: var(--sev-high); --item-dot: var(--sev-high-dot); }
.open-findings__item--medium { --item-bg: var(--sev-medium-bg); --item-fg: var(--sev-medium); --item-dot: var(--sev-medium-dot); }
.open-findings__item--low { --item-bg: var(--sev-low-bg); --item-fg: var(--sev-low); --item-dot: var(--sev-low-dot); }
.open-findings__item--info { --item-bg: var(--sev-info-bg); --item-fg: var(--sev-info); --item-dot: var(--sev-info-dot); }

.open-findings__none {
  color: var(--ok);
  font-size: var(--fs-sm);
  font-weight: 600;
}
</style>
