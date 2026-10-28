import { ref } from "vue";

import { useApi } from "@/shared/api";
import type { Scan } from "@/shared/api";
import { newIdempotencyKey, useAction } from "@/shared/lib";

/**
 * Relanza un escaneo fallido con el mismo commit y rama (`POST /repositories/{id}/scans`).
 * Un reintento tras un error de red reutiliza la clave: el servidor no duplica el escaneo.
 */
export function useRetryScan(onStarted: (scanId: string) => void) {
  const api = useApi();
  const retrying = ref<string | null>(null);
  const keys = new Map<string, string>();

  const action = useAction(async (scan: Scan) => {
    const key = keys.get(scan.id) ?? newIdempotencyKey();
    keys.set(scan.id, key);
    const result = await api.startScan(scan.repository_id, { ref: scan.ref, commit: scan.commit }, key);
    keys.delete(scan.id);
    return result;
  });

  async function retry(scan: Scan): Promise<void> {
    retrying.value = scan.id;
    const result = await action.run(scan);
    retrying.value = null;
    if (result !== undefined) onStarted(result.scan.scan_id);
  }

  return { retry, retrying, pending: action.pending, error: action.error };
}
