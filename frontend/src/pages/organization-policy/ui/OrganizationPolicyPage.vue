<script setup lang="ts">
import { computed, onMounted, ref } from "vue";

import { canAdminister, usePolicyHistory } from "@/entities/organization";
import { PolicyEditor } from "@/features/edit-policy";
import { formatTimestamp, visibleText } from "@/shared/lib";
import { ErrorNotice, Panel, SeverityBadge, StatusTag } from "@/shared/ui";
import { OrganizationShell } from "@/widgets/organization-shell";

const props = defineProps<{ organizationId: string }>();
const data = usePolicyHistory(() => props.organizationId);
onMounted(() => void data.reload());

const SCOPES: Record<string, string> = {
  NEW_FINDINGS: "Solo hallazgos nuevos del cambio",
  ALL_OPEN: "Todos los hallazgos abiertos",
};
const active = computed(() => data.versions.value?.find((v) => v.active));
const editing = ref(false);
const saved = ref<number | null>(null);

function onSaved(version: number): void {
  editing.value = false;
  saved.value = version;
  void data.reload();
}
</script>

<template>
  <OrganizationShell :organization-id="organizationId" section="Política">
    <template #default="{ org }">
      <div v-if="canAdminister(org.role) && !editing && data.versions.value" class="policy__toolbar">
        <p v-if="saved !== null" class="policy__saved" role="status">Publicada la versión {{ saved }}.</p>
        <button type="button" class="policy__edit" @click="editing = true; saved = null">
          {{ active ? "Editar política" : "Crear política" }}
        </button>
      </div>
      <Panel v-if="editing" :title="active ? `Editar política (parte de la versión ${active.version})` : 'Crear política'">
        <PolicyEditor :organization-id="organizationId" :active="active" @saved="onSaved" @cancel="editing = false" />
      </Panel>
      <ErrorNotice v-if="data.error.value" :error="data.error.value" />
      <p v-if="data.loading.value && !data.versions.value" class="policy__muted">Cargando…</p>
      <p v-else-if="data.versions.value && !active" class="policy__empty">
        La organización no tiene una política de quality gates activa. Los escaneos terminan con veredicto
        <strong>NEUTRAL</strong>: no hay nada que garantizar.
      </p>

      <template v-if="active">
        <Panel :title="`Política activa, versión ${active.version}`">
          <template #aside><span>Desde {{ formatTimestamp(active.created_at) }}</span></template>
          <section class="policy__section" aria-labelledby="policy-gates">
            <h3 id="policy-gates" class="policy__heading">Puertas</h3>
            <p v-if="active.gates.length === 0" class="policy__muted">Sin puertas: ningún recuento bloquea el cambio.</p>
            <div v-else class="policy__scroll">
              <table class="policy__table">
                <caption class="visually-hidden">Puertas de la política</caption>
                <thead><tr><th scope="col">Puerta</th><th scope="col">Ámbito</th><th scope="col">Máximo permitido</th></tr></thead>
                <tbody>
                  <tr v-for="gate in active.gates" :key="gate.id">
                    <td class="policy__mono">{{ visibleText(gate.id, 64) }}</td>
                    <td>{{ SCOPES[gate.scope] ?? gate.scope }}</td>
                    <td>
                      <span class="policy__limits">
                        <span v-for="limit in gate.max" :key="limit.severity" class="policy__limit">
                          <SeverityBadge :severity="limit.severity" />
                          <span class="policy__mono">≤ {{ limit.value }}</span>
                        </span>
                      </span>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          </section>
          <section class="policy__section" aria-labelledby="policy-sla">
            <h3 id="policy-sla" class="policy__heading">Plazos de corrección</h3>
            <p v-if="active.sla_days.length === 0" class="policy__muted">La política no define plazos.</p>
            <template v-else>
              <ul class="policy__sla">
                <li v-for="sla in active.sla_days" :key="sla.severity" class="policy__limit">
                  <SeverityBadge :severity="sla.severity" />
                  <span class="policy__mono">{{ sla.value }} días</span>
                </li>
              </ul>
              <p class="policy__muted">
                {{ active.fail_on_sla_breach ? "Superar un plazo hace fallar el quality gate." : "Superar un plazo se informa, pero no bloquea." }}
              </p>
            </template>
          </section>
        </Panel>
      </template>

      <Panel v-if="data.versions.value && data.versions.value.length > 0" title="Historial de versiones" flush>
        <div class="policy__scroll">
          <table class="policy__table">
            <caption class="visually-hidden">Versiones de la política</caption>
            <thead><tr><th scope="col">Versión</th><th scope="col">Creada</th><th scope="col">Estado</th><th scope="col" class="policy__num">Puertas</th><th scope="col" class="policy__num">Plazos</th></tr></thead>
            <tbody>
              <tr v-for="version in data.versions.value" :key="version.version">
                <td class="policy__mono">v{{ version.version }}</td>
                <td class="policy__mono">{{ formatTimestamp(version.created_at) }}</td>
                <td><StatusTag :label="version.active ? 'Activa' : 'Sustituida'" :tone="version.active ? 'ok' : 'neutral'" /></td>
                <td class="policy__num policy__mono">{{ version.gates.length }}</td>
                <td class="policy__num policy__mono">{{ version.sla_days.length }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </Panel>
    </template>
  </OrganizationShell>
</template>

<style scoped>
.policy__scroll {
  overflow-x: auto;
}

.policy__toolbar {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: var(--space-4);
}

.policy__saved {
  color: var(--ok);
  font-size: var(--fs-sm);
  font-weight: 600;
}

.policy__edit {
  height: 30px;
}

.policy__muted {
  color: var(--text-3);
  font-size: var(--fs-sm);
}

.policy__empty {
  padding: var(--space-6);
  border: 1px dashed var(--line-strong);
  border-radius: var(--radius);
  background: var(--surface);
  color: var(--text-2);
}

.policy__section {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.policy__section + .policy__section {
  padding-top: var(--space-4);
  border-top: var(--border);
}

.policy__heading {
  font-size: var(--fs-sm);
}

.policy__table {
  width: 100%;
  border-collapse: separate;
  border-spacing: 0;
}

.policy__table th:first-child,
.policy__table td:first-child {
  padding-left: var(--space-5);
}

.policy__section .policy__table {
  border: var(--border);
  border-radius: var(--radius-sm);
}

.policy__limits,
.policy__sla {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-3) var(--space-5);
  margin: 0;
  padding: 0;
  list-style: none;
}

.policy__limit {
  display: inline-flex;
  align-items: center;
  gap: var(--space-3);
}

.policy__mono {
  font-family: var(--font-mono);
  font-size: var(--fs-xs);
  font-variant-numeric: tabular-nums;
}

.policy__num {
  text-align: right;
}
</style>
