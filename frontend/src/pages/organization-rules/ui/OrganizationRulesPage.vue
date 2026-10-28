<script setup lang="ts">
import { onMounted } from "vue";

import { toneOf } from "@/entities/finding";
import { useRuleCatalog } from "@/entities/organization";
import type { RuleStat } from "@/shared/api";
import { formatTimestamp, useTextFilter, visibleText } from "@/shared/lib";
import { DataTable, ErrorNotice, Panel, SeverityBadge } from "@/shared/ui";
import type { Column } from "@/shared/ui";
import { OrganizationShell } from "@/widgets/organization-shell";

const props = defineProps<{ organizationId: string }>();
const data = useRuleCatalog(() => props.organizationId);
onMounted(() => void data.reload());

const search = useTextFilter(
  () => data.rules.value ?? [],
  (r) => [r.rule_id, r.scanner, r.category],
);

const columns: readonly Column<RuleStat>[] = [
  { key: "rule_id", header: "Regla", mono: true },
  { key: "scanner", header: "Escáner", mono: true },
  { key: "category", header: "Categoría", mono: true },
  { key: "max_severity", header: "Severidad máxima" },
  { key: "open", header: "Abiertas", numeric: true },
  { key: "total", header: "Total", numeric: true },
  { key: "repositories", header: "Repositorios", numeric: true },
  { key: "last_seen_at", header: "Última detección", mono: true, text: (r) => formatTimestamp(r.last_seen_at) },
];
</script>

<template>
  <OrganizationShell :organization-id="organizationId" section="Reglas">
    <ErrorNotice v-if="data.error.value" :error="data.error.value" />
    <Panel title="Reglas con hallazgos" flush>
      <template #aside>
        <label class="rules__search">
          <span class="visually-hidden">Buscar regla</span>
          <input v-model="search.query.value" type="search" maxlength="100" placeholder="Buscar regla, escáner o categoría" autocomplete="off" spellcheck="false">
        </label>
      </template>
      <p class="rules__note">
        Reglas de la plataforma que han producido al menos un hallazgo en esta organización, las de más abiertas primero.
      </p>
      <DataTable
        caption="Catálogo de reglas"
        caption-hidden
        bare
        :columns="columns"
        :rows="search.filtered.value"
        :row-key="(r: RuleStat) => `${r.scanner}:${r.rule_id}`"
        :tone="(r: RuleStat) => (r.open > 0 ? toneOf(r.max_severity) : undefined)"
        :loading="data.loading.value"
        :empty-text="search.query.value ? 'Ninguna regla coincide con la búsqueda.' : 'Ninguna regla ha producido hallazgos todavía.'"
      >
        <template #cell-rule_id="{ row }">
          <span class="rules__id">{{ visibleText(row.rule_id, 200) }}</span>
        </template>
        <template #cell-max_severity="{ row }">
          <SeverityBadge :severity="row.max_severity" />
        </template>
      </DataTable>
    </Panel>
  </OrganizationShell>
</template>

<style scoped>
.rules__search input {
  width: 280px;
  max-width: 100%;
  min-height: 28px;
}

.rules__note {
  padding: var(--space-3) var(--space-5);
  border-bottom: var(--border);
  color: var(--text-3);
  font-size: var(--fs-sm);
}

.rules__id {
  color: var(--text);
  font-weight: 600;
}
</style>
