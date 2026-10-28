import { reactive, ref } from "vue";

import { useApi } from "@/shared/api";
import { useAction } from "@/shared/lib";

import { buildTriageRequest } from "./validate";
import type { TriageDraft } from "./validate";

export function useTriage(findingId: () => string, onDone: () => void) {
  const api = useApi();
  const draft = reactive<TriageDraft>({ state: "FALSE_POSITIVE", justification: "", expiresLocal: "" });
  const problem = ref<string | null>(null);
  const action = useAction((request: Parameters<typeof api.triage>[1]) => api.triage(findingId(), request));

  async function submit(): Promise<void> {
    const built = buildTriageRequest(draft);
    problem.value = built.ok ? null : built.message;
    if (!built.ok) return;
    if ((await action.run(built.request)) !== undefined) onDone();
  }

  return { draft, problem, submit, pending: action.pending, error: action.error };
}
