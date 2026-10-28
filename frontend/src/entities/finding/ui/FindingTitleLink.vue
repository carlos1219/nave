<script setup lang="ts">
import { RouterLink } from "vue-router";

import type { Finding } from "@/shared/api";
import { isUuid, visibleText } from "@/shared/lib";

defineProps<{ finding: Finding }>();
</script>

<template>
  <span class="finding-link">
    <RouterLink v-if="isUuid(finding.id)" class="finding-link__title" :to="{ name: 'finding', params: { findingId: finding.id } }">
      {{ visibleText(finding.title, 200) }}
    </RouterLink>
    <span v-else class="finding-link__title">{{ visibleText(finding.title, 200) }}</span>
    <span class="finding-link__rule">{{ visibleText(finding.rule_id, 200) }}</span>
  </span>
</template>

<style scoped>
.finding-link {
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.finding-link__title {
  overflow: hidden;
  color: var(--text);
  font-weight: 600;
  text-decoration: none;
  text-overflow: ellipsis;
}

a.finding-link__title:hover {
  color: var(--accent);
  text-decoration: underline;
}

.finding-link__rule {
  overflow: hidden;
  color: var(--text-3);
  font-family: var(--font-mono);
  font-size: 11px;
  text-overflow: ellipsis;
}
</style>
