import type { PolicyRequest, PolicyVersion, Severity } from "@/shared/api";

export const POLICY_SEVERITIES: readonly Severity[] = ["CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"];
export const MAX_GATES = 32;
const GATE_ID = /^[a-z0-9][a-z0-9-]{0,62}$/;
const INTEGER = /^\d{1,7}$/;
const MAX_LIMIT = 1_000_000;
const MAX_SLA_DAYS = 3650;

export type GateScope = "NEW_FINDINGS" | "ALL_OPEN";

export interface GateForm {
  id: string;
  scope: GateScope;
  /** Texto del campo; vacío = sin límite para esa severidad. */
  max: Record<Severity, string>;
}

export interface PolicyForm {
  gates: GateForm[];
  sla: Record<Severity, string>;
  failOnSlaBreach: boolean;
}

const blank = (): Record<Severity, string> => ({ CRITICAL: "", HIGH: "", MEDIUM: "", LOW: "", INFO: "" });

export function emptyGate(index: number): GateForm {
  return { id: `puerta-${String(index + 1)}`, scope: "NEW_FINDINGS", max: blank() };
}

/** Parte de la versión activa, o de una política vacía si no hay ninguna. */
export function formFrom(version: PolicyVersion | undefined): PolicyForm {
  const sla = blank();
  for (const item of version?.sla_days ?? []) sla[item.severity] = String(item.value);
  return {
    gates: (version?.gates ?? []).map((gate) => {
      const max = blank();
      for (const item of gate.max) max[item.severity] = String(item.value);
      return { id: gate.id, scope: gate.scope, max };
    }),
    sla,
    failOnSlaBreach: version?.fail_on_sla_breach ?? false,
  };
}

function limits(values: Record<Severity, string>, min: number, max: number, field: string, problems: string[]) {
  const out: { severity: Severity; value: number }[] = [];
  for (const severity of POLICY_SEVERITIES) {
    const raw = values[severity].trim();
    if (raw === "") continue;
    const value = Number(raw);
    if (!INTEGER.test(raw) || value < min || value > max) {
      problems.push(`${field}, ${severity}: debe ser un número entero entre ${String(min)} y ${String(max)}.`);
      continue;
    }
    out.push({ severity, value });
  }
  return out;
}

/** Valida y convierte. Las mismas reglas las vuelve a imponer el servidor. */
export function toRequest(form: PolicyForm): { request: PolicyRequest | null; problems: string[] } {
  const problems: string[] = [];
  if (form.gates.length > MAX_GATES) problems.push(`Como máximo ${String(MAX_GATES)} puertas.`);
  const seen = new Set<string>();
  const gates = form.gates.map((gate, index) => {
    const label = `Puerta ${String(index + 1)}`;
    const id = gate.id.trim();
    if (!GATE_ID.test(id)) problems.push(`${label}: el identificador solo admite minúsculas, cifras y guiones.`);
    else if (seen.has(id)) problems.push(`${label}: el identificador «${id}» está repetido.`);
    seen.add(id);
    const max = limits(gate.max, 0, MAX_LIMIT, label, problems);
    if (max.length === 0) problems.push(`${label}: indica el máximo de al menos una severidad.`);
    return { id, scope: gate.scope, max };
  });
  const sla = limits(form.sla, 1, MAX_SLA_DAYS, "Plazo", problems);
  if (problems.length > 0) return { request: null, problems };
  return { request: { gates, sla_days: sla, fail_on_sla_breach: form.failOnSlaBreach }, problems };
}
