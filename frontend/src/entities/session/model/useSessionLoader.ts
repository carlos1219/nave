import { useApi } from "@/shared/api";

import { useSessionStore } from "./store";

/** Reintento manual cuando la comprobación inicial de la sesión falló por red o servidor. */
export function useSessionRetry() {
  const api = useApi();
  const session = useSessionStore();
  return { retry: () => session.ensureLoaded(api) };
}
