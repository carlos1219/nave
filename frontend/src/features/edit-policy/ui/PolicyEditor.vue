<script setup lang="ts">
import { onMounted } from "vue";

import type { PolicyVersion } from "@/shared/api";
import { ErrorNotice } from "@/shared/ui";

import { MAX_GATES, POLICY_SEVERITIES } from "../model/policyForm";
import { useEditPolicy } from "../model/useEditPolicy";

const props = defineProps<{ organizationId: string; active: PolicyVersion | undefined }>();
const emit = defineEmits<{ saved: [version: number]; cancel: [] }>();
const editor = useEditPolicy(() => props.organizationId, (version) => { emit("saved", version); });
onMounted(() => { editor.start(props.active); });

const SCOPES = [
  { value: "NEW_FINDINGS", label: "Solo hallazgos nuevos del cambio" },
  { value: "ALL_OPEN", label: "Todos los hallazgos abiertos" },
] as const;
</script>

<template>
  <form class="policy-editor" aria-label="Editar la política de quality gates" novalidate @submit.prevent="editor.save">
    <p class="policy-editor__intro">
      Guardar publica una versión nueva y deja la actual en el historial. Los escaneos que ya terminaron conservan su veredicto.
    </p>

    <fieldset class="policy-editor__section">
      <legend class="policy-editor__legend">Puertas</legend>
      <p v-if="editor.form.value.gates.length === 0" class="policy-editor__muted">
        Sin puertas, ningún recuento bloquea el cambio.
      </p>
      <div v-for="(gate, index) in editor.form.value.gates" :key="index" class="policy-editor__gate">
        <div class="policy-editor__row">
          <label class="policy-editor__field">
            <span class="policy-editor__label">Identificador</span>
            <input v-model="gate.id" class="policy-editor__mono" maxlength="63" spellcheck="false" autocomplete="off">
          </label>
          <label class="policy-editor__field policy-editor__field--grow">
            <span class="policy-editor__label">Ámbito</span>
            <select v-model="gate.scope">
              <option v-for="scope in SCOPES" :key="scope.value" :value="scope.value">{{ scope.label }}</option>
            </select>
          </label>
          <button type="button" class="policy-editor__remove" @click="editor.removeGate(index)">Quitar puerta</button>
        </div>
        <div class="policy-editor__limits" role="group" :aria-label="`Máximo permitido de la puerta ${index + 1}`">
          <label v-for="severity in POLICY_SEVERITIES" :key="severity" class="policy-editor__limit">
            <span class="policy-editor__severity">{{ severity }}</span>
            <input v-model="gate.max[severity]" class="policy-editor__mono" inputmode="numeric" maxlength="7" placeholder="sin límite">
          </label>
        </div>
      </div>
      <button type="button" class="policy-editor__add" :disabled="editor.form.value.gates.length >= MAX_GATES" @click="editor.addGate">
        Añadir puerta
      </button>
    </fieldset>

    <fieldset class="policy-editor__section">
      <legend class="policy-editor__legend">Plazos de corrección (días)</legend>
      <div class="policy-editor__limits">
        <label v-for="severity in POLICY_SEVERITIES" :key="severity" class="policy-editor__limit">
          <span class="policy-editor__severity">{{ severity }}</span>
          <input v-model="editor.form.value.sla[severity]" class="policy-editor__mono" inputmode="numeric" maxlength="4" placeholder="sin plazo">
        </label>
      </div>
      <label class="policy-editor__check">
        <input v-model="editor.form.value.failOnSlaBreach" type="checkbox">
        Superar un plazo hace fallar el quality gate
      </label>
    </fieldset>

    <ul v-if="editor.problems.value.length > 0" class="policy-editor__problems" role="alert">
      <li v-for="problem in editor.problems.value" :key="problem">{{ problem }}</li>
    </ul>
    <ErrorNotice v-if="editor.error.value" :error="editor.error.value" />

    <div class="policy-editor__actions">
      <button type="button" @click="emit('cancel')">Cancelar</button>
      <button type="submit" class="policy-editor__save" :disabled="editor.pending.value">
        {{ editor.pending.value ? "Publicando…" : "Publicar versión nueva" }}
      </button>
    </div>
  </form>
</template>

<style scoped>
.policy-editor {
  display: flex;
  flex-direction: column;
  gap: var(--space-5);
}

.policy-editor__intro,
.policy-editor__muted {
  color: var(--text-3);
  font-size: var(--fs-sm);
}

.policy-editor__section {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
  margin: 0;
  padding: 0;
  border: 0;
}

.policy-editor__legend {
  margin-bottom: var(--space-3);
  padding: 0;
  font-size: var(--fs-sm);
  font-weight: 600;
}

.policy-editor__gate {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
  padding: var(--space-4);
  border: var(--border);
  border-radius: var(--radius-sm);
  background: var(--surface-2);
}

.policy-editor__row {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-end;
  gap: var(--space-4);
}

.policy-editor__field {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.policy-editor__field--grow {
  flex: 1;
  min-width: 220px;
}

.policy-editor__label {
  color: var(--text-3);
  font-size: var(--fs-xs);
}

.policy-editor__limits {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(140px, 1fr));
  gap: var(--space-3) var(--space-4);
}

.policy-editor__limit {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.policy-editor__severity {
  color: var(--text-2);
  font-family: var(--font-mono);
  font-size: 11px;
  font-weight: 600;
}

.policy-editor__mono {
  font-family: var(--font-mono);
  font-variant-numeric: tabular-nums;
}

.policy-editor__remove,
.policy-editor__add {
  align-self: flex-start;
  height: 28px;
}

.policy-editor__remove:hover:not(:disabled) {
  border-color: var(--danger);
  color: var(--danger);
}

.policy-editor__check {
  display: inline-flex;
  align-items: center;
  gap: var(--space-3);
  font-size: var(--fs-sm);
}

.policy-editor__problems {
  margin: 0;
  padding: var(--space-4) var(--space-5);
  border: 1px solid var(--danger);
  border-radius: var(--radius-sm);
  background: var(--danger-bg);
  color: var(--danger);
  font-size: var(--fs-sm);
}

.policy-editor__actions {
  display: flex;
  justify-content: flex-end;
  gap: var(--space-3);
}

.policy-editor__save {
  border-color: var(--accent);
  background: var(--accent);
  color: var(--accent-text);
}

.policy-editor__save:hover:not(:disabled) {
  background: var(--accent-hover);
}
</style>
