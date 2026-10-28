<script setup lang="ts">
import { computed } from "vue";

import { StatusTag } from "@/shared/ui";

import { verdictView } from "../model/status";

const props = defineProps<{ verdict: string | null | undefined; emptyLabel?: string }>();
const view = computed(() => verdictView(props.verdict));
</script>

<template>
  <StatusTag v-if="view" :label="view.label" :tone="view.tone" />
  <span v-else class="verdict-tag__none">{{ emptyLabel ?? "—" }}</span>
</template>

<style scoped>
.verdict-tag__none {
  color: var(--text-3);
  font-size: var(--fs-sm);
}
</style>
