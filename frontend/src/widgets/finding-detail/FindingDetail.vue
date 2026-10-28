<script setup lang="ts">
import type { FindingDetail } from "@/shared/api";
import { formatTimestamp, visibleText } from "@/shared/lib";
import { Panel, SeverityBadge } from "@/shared/ui";

defineProps<{ finding: FindingDetail }>();
</script>

<template>
  <div class="finding">
    <div class="finding__main">
      <Panel title="Ocurrencia" flush>
        <template #aside>
          <SeverityBadge :severity="finding.severity" />
          <span v-if="finding.occurrence?.is_new" class="finding__new">Nuevo en este escaneo</span>
        </template>
        <div v-if="finding.occurrence" class="finding__occurrence">
          <p v-if="finding.occurrence.location" class="finding__location">
            {{ visibleText(finding.occurrence.location.path, 500) }}<template v-if="finding.occurrence.location.start_line !== null">:{{ finding.occurrence.location.start_line }}</template>
          </p>
          <p class="finding__message">{{ visibleText(finding.occurrence.message, 1000) }}</p>
          <pre v-if="finding.occurrence.snippet" class="finding__snippet">{{ visibleText(finding.occurrence.snippet, 4000) }}</pre>
        </div>
        <p v-else class="finding__empty">El último escaneo no registró la ocurrencia.</p>
      </Panel>
      <Panel title="Historial de triage" flush>
        <ol v-if="finding.decisions.length > 0" class="finding__decisions">
          <li v-for="d in finding.decisions" :key="d.id" class="finding__decision">
            <div class="finding__decision-head">
              <span class="finding__state">{{ d.state }}</span>
              <span class="finding__time">{{ formatTimestamp(d.decided_at) }}</span>
              <span v-if="d.expires_at" class="finding__time">caduca {{ formatTimestamp(d.expires_at) }}</span>
            </div>
            <p v-if="d.justification" class="finding__justification">{{ visibleText(d.justification, 2000) }}</p>
          </li>
        </ol>
        <p v-else class="finding__empty">Sin decisiones todavía. El hallazgo sigue abierto hasta que alguien lo clasifique.</p>
      </Panel>
    </div>
    <aside class="finding__aside" aria-label="Datos del hallazgo">
      <Panel title="Detalles">
        <dl class="finding__facts">
          <div class="finding__fact">
            <dt>Estado de triage</dt>
            <dd class="finding__mono">{{ finding.triage_state }}</dd>
          </div>
          <div class="finding__fact">
            <dt>Regla</dt>
            <dd class="finding__mono">{{ visibleText(finding.rule_id, 200) }}</dd>
          </div>
          <div class="finding__fact">
            <dt>Escáner</dt>
            <dd class="finding__mono">{{ visibleText(finding.scanner, 64) }}</dd>
          </div>
          <div class="finding__fact">
            <dt>Categoría</dt>
            <dd>{{ visibleText(finding.category, 100) }}</dd>
          </div>
          <div class="finding__fact">
            <dt>CWE</dt>
            <dd class="finding__mono">{{ finding.cwe.map((c) => visibleText(c, 32)).join(", ") || "—" }}</dd>
          </div>
          <div class="finding__fact">
            <dt>CVE</dt>
            <dd class="finding__mono">{{ finding.cve.map((c) => visibleText(c, 32)).join(", ") || "—" }}</dd>
          </div>
          <div class="finding__fact">
            <dt>Visto por primera vez</dt>
            <dd class="finding__mono">{{ formatTimestamp(finding.first_seen_at) }}</dd>
          </div>
          <div class="finding__fact">
            <dt>Visto por última vez</dt>
            <dd class="finding__mono">{{ formatTimestamp(finding.last_seen_at) }}</dd>
          </div>
        </dl>
      </Panel>
      <slot name="aside" />
    </aside>
  </div>
</template>

<style scoped>
.finding {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 340px;
  align-items: start;
  gap: var(--space-5);
}

.finding__main,
.finding__aside {
  display: flex;
  flex-direction: column;
  gap: var(--space-5);
  min-width: 0;
}

.finding__new {
  color: var(--accent);
  font-size: var(--fs-xs);
  font-weight: 600;
}

.finding__occurrence {
  display: flex;
  flex-direction: column;
}

.finding__location {
  padding: var(--space-3) var(--space-5);
  border-bottom: var(--border);
  background: var(--surface-2);
  font-family: var(--font-mono);
  font-size: var(--fs-sm);
  font-weight: 600;
  overflow-wrap: anywhere;
}

.finding__message {
  padding: var(--space-4) var(--space-5);
  max-width: 80ch;
  overflow-wrap: anywhere;
}

.finding__snippet {
  margin: 0;
  padding: var(--space-4) var(--space-5);
  background: var(--log-bg);
  color: var(--log-text);
  font-size: var(--fs-xs);
  line-height: 1.7;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.finding__empty {
  padding: var(--space-4) var(--space-5);
  color: var(--text-3);
  font-size: var(--fs-sm);
}

.finding__decisions {
  margin: 0;
  padding: 0;
  list-style: none;
}

.finding__decision {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding: var(--space-4) var(--space-5);
  border-bottom: var(--border);
}

.finding__decision:last-child {
  border-bottom: 0;
}

.finding__decision-head {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: var(--space-2) var(--space-4);
}

.finding__state {
  font-family: var(--font-mono);
  font-size: var(--fs-xs);
  font-weight: 700;
}

.finding__time {
  color: var(--text-3);
  font-family: var(--font-mono);
  font-size: var(--fs-xs);
  font-variant-numeric: tabular-nums;
}

.finding__justification {
  max-width: 80ch;
  color: var(--text-2);
  font-size: var(--fs-sm);
  overflow-wrap: anywhere;
}

.finding__facts {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.finding__fact {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}

.finding__fact dd {
  font-size: var(--fs-sm);
  overflow-wrap: anywhere;
}

.finding__mono {
  font-family: var(--font-mono);
  font-variant-numeric: tabular-nums;
}

@media (max-width: 960px) {
  .finding {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
