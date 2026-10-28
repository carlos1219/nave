<script setup lang="ts">
import { computed } from "vue";

import { VerdictTag, verdictView } from "@/entities/scan";
import type { QualityGate } from "@/shared/api";
import { visibleText } from "@/shared/lib";

const props = defineProps<{ gate: QualityGate }>();

const KIND: Record<string, string> = { GATE: "Puerta", SLA: "Plazo", TRUNCATED: "Truncado" };
const SUMMARY: Record<string, string> = {
  PASS: "El escaneo cumple la política de la organización.",
  FAIL: "La política bloquea este cambio. Revisa los motivos.",
  NEUTRAL: "Resultado no concluyente: la salida del escáner estaba incompleta.",
};

const tone = computed(() => (props.gate.evaluated ? (verdictView(props.gate.verdict)?.tone ?? "neutral") : "neutral"));
const summary = computed(() => {
  if (!props.gate.evaluated) return "La política aún no se ha evaluado para este escaneo.";
  return props.gate.verdict === null ? "Evaluado sin veredicto." : (SUMMARY[props.gate.verdict] ?? "");
});
</script>

<template>
  <section class="gate" :class="`gate--${tone}`" aria-label="Quality gate">
    <div class="gate__head">
      <p class="gate__verdict">
        <span class="gate__label">Quality gate:</span>
        <VerdictTag :verdict="gate.evaluated ? gate.verdict : null" :empty-label="gate.evaluated ? 'SIN VEREDICTO' : 'NO EVALUADO'" />
      </p>
      <p class="gate__summary">{{ summary }}</p>
    </div>
    <div v-if="gate.reasons.length > 0" class="gate__reasons-wrap">
      <table class="gate__reasons">
        <caption class="visually-hidden">Motivos del veredicto</caption>
        <thead>
          <tr>
            <th scope="col">Tipo</th>
            <th scope="col">Regla</th>
            <th scope="col">Severidad</th>
            <th scope="col" class="gate__num">Observado</th>
            <th scope="col" class="gate__num">Límite</th>
            <th scope="col">Bloquea</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(reason, index) in gate.reasons" :key="index">
            <td>{{ KIND[reason.kind] ?? visibleText(reason.kind, 16) }}</td>
            <td class="gate__mono">{{ visibleText(reason.rule, 120) }}</td>
            <td class="gate__mono">{{ reason.severity ?? "—" }}</td>
            <td class="gate__mono gate__num">{{ reason.observed }}</td>
            <td class="gate__mono gate__num">{{ reason.limit }}</td>
            <td :class="{ 'gate__blocking': reason.blocking }">{{ reason.blocking ? "Sí" : "No" }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>

<style scoped>
.gate {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
  padding: var(--space-4) var(--space-5);
  border: var(--border);
  border-left: 4px solid var(--gate-color);
  border-radius: var(--radius);
  background: var(--surface);
  box-shadow: var(--shadow-sm);
}

.gate--ok { --gate-color: var(--ok); }
.gate--fail { --gate-color: var(--danger); }
.gate--warn { --gate-color: var(--warn); }
.gate--neutral,
.gate--running { --gate-color: var(--line-strong); }

.gate__head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-2) var(--space-5);
}

.gate__verdict {
  display: flex;
  align-items: center;
  gap: var(--space-3);
}

.gate__label {
  font-weight: 600;
}

.gate__summary {
  color: var(--text-2);
  font-size: var(--fs-sm);
}

.gate__reasons-wrap {
  overflow-x: auto;
  border: var(--border);
  border-radius: var(--radius-sm);
}

.gate__reasons {
  width: 100%;
  border-collapse: separate;
  border-spacing: 0;
}

.gate__mono {
  font-family: var(--font-mono);
  font-size: var(--fs-xs);
}

.gate__num {
  text-align: right;
}

.gate__blocking {
  color: var(--danger);
  font-weight: 600;
}
</style>
