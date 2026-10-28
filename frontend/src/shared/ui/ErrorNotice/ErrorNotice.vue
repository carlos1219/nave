<script setup lang="ts">
import { computed } from "vue";

import type { ApiError } from "@/shared/api";
import { visibleText } from "@/shared/lib";

const props = defineProps<{ error: ApiError }>();

const hint = computed(() => {
  if (props.error.unauthorized) return "La sesión ha caducado.";
  if (props.error.forbidden) return "No tienes permiso para esta acción.";
  if (props.error.notFound) return "El recurso no existe o no es accesible.";
  if (props.error.tooManyRequests) return "Demasiadas peticiones; espera un momento.";
  return "";
});
const title = computed(() => visibleText(props.error.message, 200));
const requestId = computed(() => (props.error.requestId === null ? "" : visibleText(props.error.requestId, 64)));
</script>

<template>
  <p class="error-notice" role="alert">
    <strong class="error-notice__label">Error:</strong>
    {{ title }}
    <span v-if="hint"> {{ hint }}</span>
    <span v-if="requestId" class="error-notice__id"> (ref. {{ requestId }})</span>
  </p>
</template>

<style scoped>
.error-notice {
  margin: 0;
  padding: var(--space-3) var(--space-4);
  border: 1px solid var(--danger-bg);
  border-left: 3px solid var(--danger);
  border-radius: var(--radius-sm);
  background: var(--danger-bg);
  color: var(--danger);
  font-size: var(--fs-sm);
}

.error-notice__id {
  font-family: var(--font-mono);
  font-size: var(--fs-xs);
}
</style>
