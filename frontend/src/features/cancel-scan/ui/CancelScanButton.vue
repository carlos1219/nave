<script setup lang="ts">
import { isCancellable } from "@/entities/scan";
import { ErrorNotice } from "@/shared/ui";

import { useCancelScan } from "../model/useCancelScan";

const props = defineProps<{ scanId: string; status: string }>();
const emit = defineEmits<{ cancelled: [] }>();
const action = useCancelScan(
  () => props.scanId,
  () => {
    emit("cancelled");
  },
);
</script>

<template>
  <span v-if="isCancellable(status)" class="cancel-scan">
    <button type="button" class="cancel-scan__button" :disabled="action.pending.value" @click="action.cancel">
      {{ action.pending.value ? "Cancelando…" : "Cancelar escaneo" }}
    </button>
    <ErrorNotice v-if="action.error.value" :error="action.error.value" />
  </span>
</template>

<style scoped>
.cancel-scan {
  display: inline-flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-3);
}

.cancel-scan__button:not(:disabled) {
  border-color: var(--danger);
  color: var(--danger);
}

.cancel-scan__button:hover:not(:disabled) {
  border-color: var(--danger);
  background: var(--danger-bg);
}
</style>
