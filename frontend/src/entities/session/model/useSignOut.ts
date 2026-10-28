import { ref } from "vue";

import { useApi } from "@/shared/api";

import { useSessionStore } from "./store";

/** Cierre de sesión con su estado de espera y error, para que el componente solo presente. */
export function useSignOut() {
  const api = useApi();
  const session = useSessionStore();
  const pending = ref(false);
  const failed = ref(false);

  async function signOut(): Promise<void> {
    if (pending.value) return;
    pending.value = true;
    failed.value = false;
    try {
      await session.signOut(api);
    } catch {
      failed.value = true;
    } finally {
      pending.value = false;
    }
  }

  return { signOut, pending, failed };
}
