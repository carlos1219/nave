import { onScopeDispose, shallowRef, watch } from "vue";

import { useApi } from "@/shared/api";
import type { Scan } from "@/shared/api";
import { usePolling } from "@/shared/lib";

import { isTerminalStatus } from "./status";

/** Estado del escaneo al día mientras no sea terminal (CLAUDE.md §14). */
export function useScanPolling(scanId: () => string) {
  const api = useApi();
  const scan = shallowRef<Scan | undefined>(undefined);

  const poller = usePolling<Scan>({
    tick: (signal) => api.scan(scanId(), { signal }),
    onResult: (next) => {
      const previous = scan.value;
      scan.value = next;
      return (
        previous?.status !== next.status ||
        previous.finished_at !== next.finished_at ||
        previous.summary?.verdict !== next.summary?.verdict
      );
    },
    isDone: (value) => isTerminalStatus(value.status),
  });

  watch(
    scanId,
    () => {
      poller.stop();
      scan.value = undefined;
      poller.start();
    },
    { immediate: true },
  );
  onScopeDispose(poller.stop);

  return { scan, state: poller.state, error: poller.error, retry: poller.retry, refresh: poller.start };
}
