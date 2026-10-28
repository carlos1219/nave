<script setup lang="ts">
import { computed, onMounted } from "vue";

import { useFinding } from "@/entities/finding";
import { TriageForm } from "@/features/triage-finding";
import { isUuid, visibleText } from "@/shared/lib";
import { ErrorNotice, PageLayout, Panel } from "@/shared/ui";
import type { Crumb } from "@/shared/ui";
import { FindingDetail } from "@/widgets/finding-detail";

const props = defineProps<{ findingId: string }>();
const detail = useFinding(() => props.findingId);
onMounted(() => void detail.reload());

const title = computed(() => (detail.finding.value ? visibleText(detail.finding.value.title, 300) : "Hallazgo"));
const trail = computed<Crumb[]>(() => {
  const repositoryId = detail.finding.value?.repository_id;
  return [
    { label: "Organizaciones", to: { name: "home" } },
    repositoryId !== undefined && isUuid(repositoryId)
      ? { label: "Hallazgos", to: { name: "findings", params: { repositoryId } } }
      : { label: "Hallazgos" },
    { label: "Detalle" },
  ];
});
</script>

<template>
  <PageLayout :title="title" :trail="trail">
    <ErrorNotice v-if="detail.error.value" :error="detail.error.value" />
    <p v-if="detail.loading.value && !detail.finding.value" class="finding-page__loading">Cargando…</p>
    <FindingDetail v-if="detail.finding.value" :finding="detail.finding.value">
      <template #aside>
        <!-- Los controles se muestran siempre; la autorización (TRIAGER) la decide el servidor. -->
        <Panel title="Decisión de triage">
          <TriageForm :finding-id="findingId" @done="detail.reload" />
        </Panel>
      </template>
    </FindingDetail>
  </PageLayout>
</template>

<style scoped>
.finding-page__loading {
  color: var(--text-3);
}
</style>
