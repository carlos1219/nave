<script setup lang="ts">
import { toRef } from "vue";

import { useScanLogs } from "@/entities/scan";
import { ErrorNotice, LogViewer, Panel } from "@/shared/ui";

const props = defineProps<{ scanId: string; terminal: boolean }>();
const logs = useScanLogs(
  () => props.scanId,
  toRef(() => props.terminal),
);
</script>

<template>
  <Panel title="Registro del escaneo" flush>
    <template #aside>
      <span>{{ logs.lines.value.length }} líneas</span>
      <span v-if="!terminal">En directo</span>
      <button v-if="logs.state.value === 'failed'" type="button" class="scan-logs__retry" @click="logs.retry">Reintentar</button>
    </template>
    <div v-if="logs.error.value" class="scan-logs__error">
      <ErrorNotice :error="logs.error.value" />
    </div>
    <LogViewer :lines="logs.lines.value" label="Registros del escaneo" />
  </Panel>
</template>

<style scoped>
.scan-logs__retry {
  height: 26px;
}

.scan-logs__error {
  padding: var(--space-4) var(--space-5) 0;
}
</style>
