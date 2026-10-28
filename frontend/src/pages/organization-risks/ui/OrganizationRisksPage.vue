<script setup lang="ts">
import { computed, onMounted } from "vue";

import { FindingTitleLink, toneOf } from "@/entities/finding";
import { useAcceptedRisks } from "@/entities/organization";
import { ExportLinks } from "@/features/export-findings";
import type { FindingRow } from "@/shared/api";
import { formatTimestamp, visibleText } from "@/shared/lib";
import { DataTable, ErrorNotice, Panel, SeverityBadge, StatusTag } from "@/shared/ui";
import type { Column } from "@/shared/ui";
import { OrganizationShell } from "@/widgets/organization-shell";

import { expiryView } from "../model/expiry";

const props = defineProps<{ organizationId: string }>();
const data = useAcceptedRisks(() => props.organizationId);
onMounted(() => void data.reload());

const now = Date.now();
const soon = computed(() => (data.risks.value ?? []).filter((r) => expiryView(r.finding.triage_expires_at, now).tone !== "neutral").length);

const columns: readonly Column<FindingRow>[] = [
  { key: "severity", header: "Severidad" },
  { key: "title", header: "Hallazgo" },
  { key: "repository", header: "Repositorio", mono: true, text: (r) => r.repository.name },
  { key: "expires", header: "Caducidad" },
  { key: "expires_at", header: "Fecha", mono: true, text: (r) => (r.finding.triage_expires_at ? formatTimestamp(r.finding.triage_expires_at) : "—") },
  { key: "justification", header: "Justificación", text: (r) => r.justification ?? "—" },
];
</script>

<template>
  <OrganizationShell :organization-id="organizationId" section="Riesgos aceptados">
    <ErrorNotice v-if="data.error.value" :error="data.error.value" />
    <Panel title="Riesgos aceptados" flush>
      <template #aside>
        <template v-if="data.risks.value">
          <span>{{ data.risks.value.length }} en total</span>
          <span>{{ soon }} caducados o a punto de caducar</span>
        </template>
        <ExportLinks :source="{ kind: 'organization', organizationId, view: 'accepted-risks' }" />
      </template>
      <DataTable
        caption="Riesgos aceptados, el que caduca antes primero"
        caption-hidden
        bare
        :columns="columns"
        :rows="data.risks.value ?? []"
        :row-key="(r: FindingRow) => r.finding.id"
        :tone="(r: FindingRow) => toneOf(r.finding.severity)"
        :loading="data.loading.value"
        empty-text="No hay riesgos aceptados en esta organización."
      >
        <template #cell-severity="{ row }">
          <SeverityBadge :severity="row.finding.severity" />
        </template>
        <template #cell-title="{ row }">
          <FindingTitleLink :finding="row.finding" />
        </template>
        <template #cell-expires="{ row }">
          <StatusTag v-bind="expiryView(row.finding.triage_expires_at, now)" />
        </template>
        <template #cell-justification="{ row }">
          <span class="risks__justification">{{ row.justification ? visibleText(row.justification, 300) : "—" }}</span>
        </template>
      </DataTable>
    </Panel>
  </OrganizationShell>
</template>

<style scoped>
.risks__justification {
  color: var(--text-2);
}
</style>
