<script setup lang="ts">
import { ErrorNotice } from "@/shared/ui";

import { useStartScan } from "../model/useStartScan";

const props = defineProps<{ repositoryId: string }>();
const emit = defineEmits<{ started: [scanId: string] }>();

const form = useStartScan(
  () => props.repositoryId,
  (id) => {
    emit("started", id);
  },
);
</script>

<template>
  <form class="start-scan" aria-label="Iniciar escaneo" novalidate @submit.prevent="form.submit">
    <div class="start-scan__fields">
      <label class="start-scan__field">
        Referencia
        <input v-model="form.ref.value" class="start-scan__input" type="text" maxlength="200" autocomplete="off" spellcheck="false" placeholder="main" required>
        <span v-if="form.problems.value.ref" class="start-scan__problem">{{ form.problems.value.ref }}</span>
      </label>
      <label class="start-scan__field start-scan__field--wide">
        Commit (SHA completo)
        <input v-model="form.commit.value" class="start-scan__input" type="text" maxlength="40" autocomplete="off" spellcheck="false" required>
        <span v-if="form.problems.value.commit" class="start-scan__problem">{{ form.problems.value.commit }}</span>
      </label>
      <button type="submit" class="start-scan__submit" :disabled="form.pending.value">
        {{ form.pending.value ? "Enviando…" : "Iniciar escaneo" }}
      </button>
    </div>
    <ErrorNotice v-if="form.error.value" :error="form.error.value" />
  </form>
</template>

<style scoped>
.start-scan {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.start-scan__fields {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-start;
  gap: var(--space-4);
}

.start-scan__field {
  display: flex;
  flex: 1 1 200px;
  flex-direction: column;
  gap: var(--space-2);
  max-width: 280px;
}

.start-scan__field--wide {
  flex-basis: 360px;
  max-width: 440px;
}

.start-scan__input {
  font-family: var(--font-mono);
}

/* Alinea el botón con los campos, no con sus etiquetas. */
.start-scan__submit {
  margin-top: 22px;
}

.start-scan__problem {
  color: var(--danger);
  font-size: var(--fs-xs);
  font-weight: 600;
}
</style>
