<script setup lang="ts">
import { TRIAGE_STATES } from "@/entities/finding";
import { ErrorNotice } from "@/shared/ui";

import { useTriage } from "../model/useTriage";
import { MAX_JUSTIFICATION } from "../model/validate";

const props = defineProps<{ findingId: string }>();
const emit = defineEmits<{ done: [] }>();
const form = useTriage(
  () => props.findingId,
  () => {
    emit("done");
  },
);
</script>

<template>
  <form class="triage" aria-label="Triage del finding" novalidate @submit.prevent="form.submit">
    <fieldset class="triage__states">
      <legend class="triage__legend">Nuevo estado</legend>
      <label v-for="s in TRIAGE_STATES" :key="s" class="triage__state">
        <input v-model="form.draft.state" class="triage__radio" type="radio" name="triage-state" :value="s">
        {{ s }}
      </label>
    </fieldset>
    <label class="triage__field">
      Justificación
      <textarea v-model="form.draft.justification" rows="4" :maxlength="MAX_JUSTIFICATION" />
    </label>
    <label v-if="form.draft.state === 'ACCEPTED_RISK'" class="triage__field">
      Caduca
      <input v-model="form.draft.expiresLocal" type="datetime-local">
    </label>
    <p v-if="form.problem.value" class="triage__problem" role="alert">{{ form.problem.value }}</p>
    <button type="submit" class="triage__submit" :disabled="form.pending.value">
      {{ form.pending.value ? "Guardando…" : "Guardar decisión" }}
    </button>
    <ErrorNotice v-if="form.error.value" :error="form.error.value" />
  </form>
</template>

<style scoped>
.triage {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.triage__states {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--space-2);
  margin: 0;
  padding: 0;
  border: 0;
}

.triage__legend {
  margin-bottom: var(--space-2);
  padding: 0;
  color: var(--text-2);
  font-size: var(--fs-sm);
  font-weight: 500;
}

.triage__state {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  height: 32px;
  padding: 0 var(--space-3);
  border: 1px solid var(--line-strong);
  border-radius: var(--radius-sm);
  color: var(--text);
  font-family: var(--font-mono);
  font-size: 11px;
  font-weight: 600;
  cursor: pointer;
}

.triage__state:hover {
  border-color: var(--text-3);
}

.triage__state:has(:checked) {
  border-color: var(--accent);
  background: var(--accent-soft);
  color: var(--accent);
}

.triage__state:has(:focus-visible) {
  outline: 2px solid var(--focus);
  outline-offset: 2px;
}

.triage__radio {
  margin: 0;
  accent-color: var(--accent);
}

.triage__radio:focus-visible {
  outline: 0;
}

.triage__field {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.triage__submit {
  align-self: stretch;
}

.triage__problem {
  margin: 0;
  color: var(--danger);
  font-size: var(--fs-sm);
  font-weight: 600;
}
</style>
