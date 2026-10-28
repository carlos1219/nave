import { ref } from "vue";

import { useApi } from "@/shared/api";
import { newIdempotencyKey, useAction } from "@/shared/lib";

import { validateCommit, validateRef } from "./validate";

/**
 * La clave de idempotencia es una por intento: un reintento con los mismos datos la reutiliza
 * (el servidor no duplica el escaneo) y cualquier cambio de datos o un éxito generan otra.
 */
export function useStartScan(repositoryId: () => string, onStarted: (scanId: string) => void) {
  const api = useApi();
  const ref_ = ref("");
  const commit = ref("");
  const problems = ref<{ ref: string | null; commit: string | null }>({ ref: null, commit: null });
  let key: string | null = null;
  let keyFor = "";

  const action = useAction(async (branch: string, sha: string) => {
    const signature = `${repositoryId()}|${branch}|${sha}`;
    if (key === null || keyFor !== signature) {
      key = newIdempotencyKey();
      keyFor = signature;
    }
    const result = await api.startScan(repositoryId(), { ref: branch, commit: sha.toLowerCase() }, key);
    key = null;
    return result;
  });

  async function submit(): Promise<void> {
    const branch = ref_.value.trim();
    const sha = commit.value.trim();
    problems.value = { ref: validateRef(branch), commit: validateCommit(sha) };
    if (problems.value.ref !== null || problems.value.commit !== null) return;
    const result = await action.run(branch, sha);
    if (result !== undefined) onStarted(result.scan.scan_id);
  }

  return { ref: ref_, commit, problems, submit, pending: action.pending, error: action.error };
}
