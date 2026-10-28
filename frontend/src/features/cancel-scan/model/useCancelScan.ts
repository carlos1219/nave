import { useApi } from "@/shared/api";
import { useAction } from "@/shared/lib";

export function useCancelScan(scanId: () => string, onDone: () => void) {
  const api = useApi();
  const action = useAction(() => api.cancelScan(scanId()));
  async function cancel(): Promise<void> {
    if ((await action.run()) !== undefined) onDone();
  }
  return { cancel, pending: action.pending, error: action.error };
}
