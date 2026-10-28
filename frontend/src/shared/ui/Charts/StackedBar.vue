<script setup lang="ts">
import { computed } from "vue";

import { stackSegments } from "@/shared/lib";

import type { Tone } from "../DataTable/types";

// Las cifras van siempre escritas junto a la barra (§13): esto es solo el refuerzo visual.
const props = defineProps<{ segments: readonly { key: Tone; value: number }[]; max: number; label: string }>();

const WIDTH = 100;
const parts = computed(() => stackSegments(props.segments, WIDTH, props.max));
</script>

<template>
  <svg class="stacked-bar" :viewBox="`0 0 ${WIDTH} 8`" preserveAspectRatio="none" role="img" :aria-label="label">
    <rect class="stacked-bar__track" x="0" y="0" :width="WIDTH" height="8" />
    <rect
      v-for="part in parts"
      :key="part.key"
      class="stacked-bar__part"
      :class="`stacked-bar__part--${part.key}`"
      :x="part.x"
      y="0"
      :width="part.width"
      height="8"
    />
  </svg>
</template>

<style scoped>
.stacked-bar {
  display: block;
  width: 100%;
  height: 10px;
}

.stacked-bar__track {
  fill: var(--surface-2);
}

.stacked-bar__part--critical { fill: var(--sev-critical-dot); }
.stacked-bar__part--high { fill: var(--sev-high-dot); }
.stacked-bar__part--medium { fill: var(--sev-medium-dot); }
.stacked-bar__part--low { fill: var(--sev-low-dot); }
.stacked-bar__part--info { fill: var(--sev-info-dot); }
</style>
