import { ref } from "vue";

import { useApi } from "@/shared/api";
import type { PolicyVersion } from "@/shared/api";
import { useAction } from "@/shared/lib";

import { emptyGate, formFrom, MAX_GATES, toRequest } from "./policyForm";
import type { PolicyForm } from "./policyForm";

/** Edita una copia de la política activa y la publica como versión nueva. */
export function useEditPolicy(organizationId: () => string, onSaved: (version: number) => void) {
  const api = useApi();
  const form = ref<PolicyForm>(formFrom(undefined));
  const problems = ref<string[]>([]);
  const action = useAction((request: Parameters<typeof api.updatePolicy>[1]) => api.updatePolicy(organizationId(), request));

  function start(active: PolicyVersion | undefined): void {
    form.value = formFrom(active);
    problems.value = [];
    action.clear();
  }

  function addGate(): void {
    if (form.value.gates.length < MAX_GATES) form.value.gates.push(emptyGate(form.value.gates.length));
  }

  function removeGate(index: number): void {
    form.value.gates.splice(index, 1);
  }

  async function save(): Promise<void> {
    const { request, problems: found } = toRequest(form.value);
    problems.value = found;
    if (request === null) return;
    const version = await action.run(request);
    if (version !== undefined) onSaved(version);
  }

  return { form, problems, start, addGate, removeGate, save, pending: action.pending, error: action.error };
}
