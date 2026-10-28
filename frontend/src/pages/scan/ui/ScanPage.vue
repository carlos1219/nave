<script setup lang="ts">
import { computed, watch } from "vue";
import { RouterLink } from "vue-router";

import { isTerminalStatus, ScanStatusTag, useQualityGate, useScanPolling } from "@/entities/scan";
import { CancelScanButton } from "@/features/cancel-scan";
import { isUuid, visibleText } from "@/shared/lib";
import { ErrorNotice, PageLayout } from "@/shared/ui";
import type { Crumb } from "@/shared/ui";
import { QualityGatePanel } from "@/widgets/quality-gate-panel";
import { ScanLogsPanel } from "@/widgets/scan-logs-panel";
import { ScanOverview } from "@/widgets/scan-overview";

const props = defineProps<{ scanId: string }>();
const polling = useScanPolling(() => props.scanId);
const gate = useQualityGate(() => props.scanId);
const terminal = computed(() => polling.scan.value !== undefined && isTerminalStatus(polling.scan.value.status));
const title = computed(() => (polling.scan.value ? `Escaneo ${visibleText(polling.scan.value.commit.slice(0, 12), 12)}` : "Escaneo"));
const trail: readonly Crumb[] = [{ label: "Organizaciones", to: { name: "home" } }, { label: "Escaneo" }];

watch(terminal, (done) => {
  if (done) void gate.reload();
});
</script>

<template>
  <PageLayout :title="title" :trail="trail">
    <template v-if="polling.scan.value" #meta>
      <ScanStatusTag :status="polling.scan.value.status" />
      <span class="scan-page__mono">{{ visibleText(polling.scan.value.ref, 200) }}</span>
    </template>
    <template #actions>
      <CancelScanButton v-if="polling.scan.value" :scan-id="scanId" :status="polling.scan.value.status" @cancelled="polling.refresh" />
      <RouterLink
        v-if="polling.scan.value && isUuid(polling.scan.value.repository_id)"
        class="action-link"
        :to="{ name: 'findings', params: { repositoryId: polling.scan.value.repository_id } }"
      >
        Ver hallazgos
      </RouterLink>
    </template>
    <ErrorNotice v-if="polling.error.value" :error="polling.error.value" />
    <div v-if="polling.state.value === 'failed'">
      <button type="button" @click="polling.retry">Reintentar</button>
    </div>
    <p v-if="!polling.scan.value && !polling.error.value" class="scan-page__loading">Cargando…</p>
    <template v-if="polling.scan.value">
      <QualityGatePanel v-if="gate.gate.value" :gate="gate.gate.value" />
      <ErrorNotice v-if="gate.error.value" :error="gate.error.value" />
      <ScanOverview :scan="polling.scan.value" />
      <ScanLogsPanel :scan-id="scanId" :terminal="terminal" />
    </template>
  </PageLayout>
</template>

<style scoped>
.scan-page__mono {
  font-family: var(--font-mono);
  font-size: var(--fs-xs);
}

.scan-page__loading {
  color: var(--text-3);
}
</style>
