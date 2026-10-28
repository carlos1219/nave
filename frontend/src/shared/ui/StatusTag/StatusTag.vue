<script setup lang="ts">
import type { StatusTone } from "./types";

// El texto es la fuente de verdad; el color solo lo refuerza (§13).
defineProps<{ label: string; tone: StatusTone }>();
</script>

<template>
  <span class="status-tag" :class="`status-tag--${tone}`">{{ label }}</span>
</template>

<style scoped>
.status-tag {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 0 var(--space-3);
  border: 1px solid var(--tag-line);
  border-radius: var(--radius-sm);
  background: var(--tag-bg);
  color: var(--tag-fg);
  font-size: var(--fs-xs);
  font-weight: 600;
  line-height: 20px;
  white-space: nowrap;
}

.status-tag::before {
  content: "";
  width: 6px;
  height: 6px;
  border-radius: var(--radius-pill);
  background: currentcolor;
}

.status-tag--running { --tag-bg: var(--accent-soft); --tag-fg: var(--accent); --tag-line: var(--accent-soft); }
.status-tag--ok { --tag-bg: var(--ok-bg); --tag-fg: var(--ok); --tag-line: var(--ok-bg); }
.status-tag--fail { --tag-bg: var(--danger-bg); --tag-fg: var(--danger); --tag-line: var(--danger-bg); }
.status-tag--warn { --tag-bg: var(--warn-bg); --tag-fg: var(--warn); --tag-line: var(--warn-bg); }
.status-tag--neutral { --tag-bg: var(--surface-2); --tag-fg: var(--text-2); --tag-line: var(--line); }
</style>
