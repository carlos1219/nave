<script setup lang="ts">
import { computed, onMounted } from "vue";
import { RouterLink } from "vue-router";

import { OpenFindingsSummary } from "@/entities/finding";
import { ScanStatusTag, VerdictTag } from "@/entities/scan";
import { formatTimestamp, isUuid, visibleText } from "@/shared/lib";
import { ErrorNotice, LoadMore, Panel } from "@/shared/ui";
import type { Crumb } from "@/shared/ui";
import { FindingsTable } from "@/widgets/findings-table";
import { OrganizationShell } from "@/widgets/organization-shell";

import { usePullRequest } from "../model/usePullRequest";

const props = defineProps<{ organizationId: string; scanId: string }>();
const { scan, findings } = usePullRequest(() => props.scanId);
onMounted(() => void scan.reload());

const current = computed(() => scan.data.value);
const section = computed(() => {
  const number = current.value?.pull_request_number;
  return number === null || number === undefined ? "Pull request" : `PR #${String(number)}`;
});
const parent = computed<Crumb>(() => ({
  label: "Pull requests",
  to: { name: "organization-pulls", params: { organizationId: props.organizationId } },
}));
</script>

<template>
  <OrganizationShell :organization-id="organizationId" :section="section" :parent="parent">
    <ErrorNotice v-if="scan.error.value" :error="scan.error.value" />
    <Panel v-if="current" :title="section">
      <template #aside>
        <RouterLink v-if="isUuid(current.id)" :to="{ name: 'scan', params: { scanId: current.id } }">Ver el escaneo completo</RouterLink>
      </template>
      <dl class="pr__facts">
        <div class="pr__fact">
          <dt>Rama</dt>
          <dd class="pr__mono">{{ visibleText(current.ref, 255) }}</dd>
        </div>
        <div class="pr__fact">
          <dt>Commit</dt>
          <dd class="pr__mono">{{ visibleText(current.commit.slice(0, 12), 12) }}</dd>
        </div>
        <div class="pr__fact">
          <dt>Escaneo</dt>
          <dd><ScanStatusTag :status="current.status" /></dd>
        </div>
        <div class="pr__fact">
          <dt>Quality gate</dt>
          <dd><VerdictTag :verdict="current.summary?.verdict" /></dd>
        </div>
        <div class="pr__fact">
          <dt>En cola</dt>
          <dd class="pr__mono">{{ formatTimestamp(current.queued_at) }}</dd>
        </div>
        <div class="pr__fact pr__fact--wide">
          <dt>Hallazgos nuevos</dt>
          <dd>
            <OpenFindingsSummary v-if="current.summary" :counts="current.summary.new" />
            <span v-else class="pr__muted">Aún no hay resultados.</span>
          </dd>
        </div>
      </dl>
    </Panel>

    <ErrorNotice v-if="findings.error.value" :error="findings.error.value" />
    <Panel title="Lo que introduce esta PR" flush>
      <template #aside>
        <span>{{ findings.items.value.length }}{{ findings.hasMore.value ? "+" : "" }}</span>
      </template>
      <FindingsTable :findings="findings.items.value" :loading="findings.loading.value || scan.loading.value" />
    </Panel>
    <LoadMore :has-more="findings.hasMore.value" :loading="findings.loadingMore.value" :capped="findings.capped.value" @more="findings.loadMore" />
  </OrganizationShell>
</template>

<style scoped>
.pr__facts {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
  gap: var(--space-4) var(--space-6);
  margin: 0;
}

.pr__fact {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  min-width: 0;
}

.pr__fact--wide {
  grid-column: 1 / -1;
}

.pr__fact dt {
  color: var(--text-3);
  font-size: var(--fs-xs);
}

.pr__fact dd {
  margin: 0;
  min-width: 0;
}

.pr__mono {
  overflow: hidden;
  font-family: var(--font-mono);
  font-size: var(--fs-sm);
  font-variant-numeric: tabular-nums;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.pr__muted {
  color: var(--text-3);
  font-size: var(--fs-sm);
}
</style>
