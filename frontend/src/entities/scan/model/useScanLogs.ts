import { onScopeDispose, shallowRef, watch } from "vue";
import type { Ref } from "vue";

import { useApi } from "@/shared/api";
import type { ScanLogEntry } from "@/shared/api";
import { usePolling } from "@/shared/lib";

const BATCH = 200;
/** El visor solo pinta las últimas 5000; aquí se guarda lo justo para eso. */
const MAX_KEPT = 5000;
/** Cada mensaje se acota al guardarlo: 5000 líneas de tamaño libre no caben en memoria. */
const MAX_MESSAGE = 2000;

/**
 * Logs incrementales por número de secuencia. Sigue consultando mientras el escaneo no sea
 * terminal y, una vez terminal, hasta vaciar lo pendiente.
 */
export function useScanLogs(scanId: () => string, terminal: Ref<boolean>) {
  const api = useApi();
  const lines = shallowRef<readonly ScanLogEntry[]>([]);
  let lastSeq = 0;

  const poller = usePolling<readonly ScanLogEntry[]>({
    tick: async (signal) => (await api.scanLogs(scanId(), { afterSeq: lastSeq, limit: BATCH }, { signal })).items,
    onResult: (batch) => {
      const fresh = batch
        .filter((entry) => entry.seq > lastSeq)
        .map((entry) => (entry.message.length > MAX_MESSAGE ? { ...entry, message: entry.message.slice(0, MAX_MESSAGE) } : entry));
      if (fresh.length === 0) return false;
      lastSeq = Math.max(lastSeq, ...fresh.map((entry) => entry.seq));
      lines.value = [...lines.value, ...fresh].slice(-MAX_KEPT);
      return true;
    },
    isDone: (batch) => terminal.value && batch.length < BATCH,
  });

  watch(
    scanId,
    () => {
      poller.stop();
      lines.value = [];
      lastSeq = 0;
      poller.start();
    },
    { immediate: true },
  );
  // Si el escaneo termina mientras el sondeo duerme, se vacía lo pendiente sin esperar al tope.
  watch(terminal, (done) => {
    if (done && poller.state.value !== "failed") {
      poller.stop();
      poller.start();
    }
  });
  onScopeDispose(poller.stop);

  return { lines, state: poller.state, error: poller.error, retry: poller.retry };
}
