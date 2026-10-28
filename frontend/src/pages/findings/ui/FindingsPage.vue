<script setup lang="ts">
import { computed, watch } from "vue";
import { useRoute, useRouter } from "vue-router";

import { useFindings } from "@/entities/finding";
import { ExportLinks } from "@/features/export-findings";
import type { ExportTarget } from "@/features/export-findings";
import {
  FindingsFilterBar,
  parseFilter,
  serializeFilter,
  useFindingsFilter,
} from "@/features/findings-filter";
import { ErrorNotice, LoadMore, PageLayout, Panel } from "@/shared/ui";
import type { Crumb } from "@/shared/ui";
import { FindingsTable } from "@/widgets/findings-table";

const props = defineProps<{ repositoryId: string }>();
const route = useRoute();
const router = useRouter();

const filter = useFindingsFilter(parseFilter(route.query));
const findings = useFindings(() => props.repositoryId, filter.apiFilter);
const scanners = computed(() => [...new Set([...filter.state.scanner, ...findings.findings.value.map((f) => f.scanner)])]);
const trail: readonly Crumb[] = [{ label: "Organizaciones", to: { name: "home" } }, { label: "Hallazgos" }];
// La descarga aplica severidad, estado y escáner; «solo nuevos» depende de un escaneo y no se exporta.
const exportTarget = computed<ExportTarget>(() => ({
  kind: "repository",
  repositoryId: props.repositoryId,
  filter: { severity: filter.state.severity, state: filter.state.state, scanner: filter.state.scanner },
}));

// La URL refleja el filtro ya depurado; nunca se copia la consulta original.
watch(
  () => serializeFilter(filter.state),
  (query) => {
    void router.replace({ query });
  },
  { deep: true, immediate: true },
);
</script>

<template>
  <PageLayout title="Hallazgos" :trail="trail">
    <FindingsFilterBar
      :filter="filter.state"
      :scanners="scanners"
      :active="filter.active.value"
      @severity="filter.toggleSeverity"
      @state="filter.toggleState"
      @scanner="filter.toggleScanner"
      @only-new="filter.setOnlyNew"
      @reset="filter.reset"
    />
    <ErrorNotice v-if="findings.error.value" :error="findings.error.value" />
    <Panel title="Resultados" flush>
      <template #aside>
        <span>{{ findings.findings.value.length }}{{ findings.hasMore.value ? "+" : "" }} mostrados</span>
        <ExportLinks :source="exportTarget" />
      </template>
      <FindingsTable :findings="findings.findings.value" :loading="findings.loading.value" />
    </Panel>
    <LoadMore :has-more="findings.hasMore.value" :loading="findings.loadingMore.value" :capped="findings.capped.value" @more="findings.loadMore" />
  </PageLayout>
</template>
