<script setup lang="ts">
import { computed, onMounted } from "vue";
import { RouterLink } from "vue-router";

import { useScannerReport } from "@/entities/organization";
import { VerdictTag } from "@/entities/scan";
import type { ScanActivity, ToolUsage } from "@/shared/api";
import { formatTimestamp, isUuid, visibleText } from "@/shared/lib";
import { DataTable, ErrorNotice, Panel, StatusTag } from "@/shared/ui";
import type { Column } from "@/shared/ui";
import { OrganizationShell } from "@/widgets/organization-shell";

const props = defineProps<{ organizationId: string }>();
const data = useScannerReport(() => props.organizationId);
onMounted(() => void data.reload());

/** Por escáner, la versión usada más recientemente es la vigente; el resto, anteriores. */
const current = computed(() => {
  const latest = new Map<string, ToolUsage>();
  for (const usage of data.report.value?.versions ?? []) {
    const known = latest.get(usage.scanner);
    if (known === undefined || usage.last_used_at > known.last_used_at) latest.set(usage.scanner, usage);
  }
  return new Set([...latest.values()].map((u) => `${u.scanner}|${u.version}|${u.image_digest}`));
});
const isCurrent = (u: ToolUsage): boolean => current.value.has(`${u.scanner}|${u.version}|${u.image_digest}`);
const shortDigest = (digest: string): string => visibleText(digest.replace(/^sha256:/, "").slice(0, 12), 12);

const versionColumns: readonly Column<ToolUsage>[] = [
  { key: "scanner", header: "Escáner", mono: true },
  { key: "version", header: "Versión", mono: true },
  { key: "image_digest", header: "Imagen", mono: true, text: (u) => shortDigest(u.image_digest) },
  { key: "state", header: "Uso" },
  { key: "scans", header: "Escaneos", numeric: true },
  { key: "first_used_at", header: "Primer uso", mono: true, text: (u) => formatTimestamp(u.first_used_at) },
  { key: "last_used_at", header: "Último uso", mono: true, text: (u) => formatTimestamp(u.last_used_at) },
];
const recentColumns: readonly Column<ScanActivity>[] = [
  { key: "commit", header: "Commit", mono: true },
  { key: "repository", header: "Repositorio", mono: true, text: (a) => a.repository.name },
  { key: "tools", header: "Escáneres" },
  { key: "verdict", header: "Quality gate" },
  { key: "finished_at", header: "Terminó", mono: true, text: (a) => (a.scan.finished_at ? formatTimestamp(a.scan.finished_at) : "—") },
];
</script>

<template>
  <OrganizationShell :organization-id="organizationId" section="Escáneres">
    <ErrorNotice v-if="data.error.value" :error="data.error.value" />
    <Panel title="Versiones en uso" flush>
      <template v-if="data.report.value" #aside>
        <span>Últimos 500 escaneos completados</span>
      </template>
      <p v-if="data.report.value && data.report.value.without_tools > 0" class="scanners__note">
        {{ data.report.value.without_tools }} escaneos de la muestra son anteriores al registro de versiones y no figuran aquí.
      </p>
      <DataTable
        caption="Versiones de escáner usadas en la organización"
        caption-hidden
        bare
        :columns="versionColumns"
        :rows="data.report.value?.versions ?? []"
        :row-key="(u: ToolUsage) => `${u.scanner}|${u.version}|${u.image_digest}`"
        :loading="data.loading.value"
        empty-text="Aún no hay escaneos completados con registro de versiones."
      >
        <template #cell-state="{ row }">
          <StatusTag :label="isCurrent(row) ? 'Vigente' : 'Anterior'" :tone="isCurrent(row) ? 'ok' : 'neutral'" />
        </template>
      </DataTable>
      <p class="scanners__note scanners__note--end">
        La antigüedad de la base de datos de vulnerabilidades de Trivy no se registra todavía: solo la conoce el contenedor del escáner.
      </p>
    </Panel>

    <Panel title="Escaneos recientes" flush>
      <DataTable
        caption="Escaneos completados más recientes con sus escáneres"
        caption-hidden
        bare
        :columns="recentColumns"
        :rows="data.report.value?.recent ?? []"
        :row-key="(a: ScanActivity) => a.scan.id"
        :loading="data.loading.value"
        empty-text="Aún no hay escaneos completados."
      >
        <template #cell-commit="{ row }">
          <RouterLink v-if="isUuid(row.scan.id)" class="scanners__link" :to="{ name: 'scan', params: { scanId: row.scan.id } }">
            {{ visibleText(row.scan.commit.slice(0, 12), 12) }}
          </RouterLink>
          <template v-else>{{ visibleText(row.scan.commit.slice(0, 12), 12) }}</template>
        </template>
        <template #cell-tools="{ row }">
          <ul v-if="row.scan.summary && row.scan.summary.tools.length > 0" class="scanners__tools">
            <li v-for="tool in row.scan.summary.tools" :key="tool.scanner" class="scanners__tool">
              {{ visibleText(tool.scanner, 32) }} {{ visibleText(tool.version, 64) }}
              <span class="scanners__count">{{ tool.findings }} hallazgos</span>
            </li>
          </ul>
          <span v-else class="scanners__muted">Sin registro</span>
        </template>
        <template #cell-verdict="{ row }">
          <VerdictTag :verdict="row.scan.summary?.verdict" />
        </template>
      </DataTable>
    </Panel>
  </OrganizationShell>
</template>

<style scoped>
.scanners__note {
  padding: var(--space-3) var(--space-5);
  border-bottom: var(--border);
  color: var(--text-3);
  font-size: var(--fs-xs);
}

.scanners__note--end {
  border-top: var(--border);
  border-bottom: 0;
}

.scanners__link {
  font-weight: 600;
  text-decoration: none;
}

.scanners__link:hover {
  text-decoration: underline;
}

.scanners__tools {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2) var(--space-4);
  margin: 0;
  padding: 0;
  list-style: none;
  font-family: var(--font-mono);
  font-size: var(--fs-xs);
  font-variant-numeric: tabular-nums;
}

.scanners__count {
  color: var(--text-3);
}

.scanners__muted {
  color: var(--text-3);
  font-size: var(--fs-xs);
}
</style>
