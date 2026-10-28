<script setup lang="ts">
import { computed } from "vue";

import { useApi } from "@/shared/api";

import { exportLinks } from "../model/exportLinks";
import type { ExportTarget } from "../model/exportLinks";

const props = defineProps<{ source: ExportTarget }>();
const api = useApi();
const links = computed(() => exportLinks(api, props.source));
</script>

<template>
  <span v-if="links.length > 0" class="export-links">
    <span class="export-links__label">Descargar</span>
    <a v-for="link in links" :key="link.format" class="export-links__link" :href="link.href" download>{{ link.label }}</a>
  </span>
</template>

<style scoped>
.export-links {
  display: inline-flex;
  align-items: center;
  gap: var(--space-3);
}

.export-links__label {
  color: var(--text-3);
  font-size: var(--fs-xs);
}

.export-links__link {
  display: inline-flex;
  align-items: center;
  height: 26px;
  padding: 0 var(--space-4);
  border: var(--border);
  border-radius: var(--radius-sm);
  background: var(--surface);
  color: var(--text);
  font-family: var(--font-mono);
  font-size: var(--fs-xs);
  font-weight: 600;
  text-decoration: none;
}

.export-links__link:hover {
  border-color: var(--accent);
  color: var(--accent);
}
</style>
