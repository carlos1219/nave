import { defineStore } from "pinia";
import { computed, ref } from "vue";

import { ApiError } from "@/shared/api";
import type { Api, Session } from "@/shared/api";
import { visibleText } from "@/shared/lib";

export type SessionStatus = "unknown" | "loading" | "authenticated" | "anonymous" | "error";

const MAX_NAME = 80;
const FALLBACK_NAME = "Usuario";

/**
 * Única pieza de estado global (CLAUDE.md §14). El token CSRF NO está en el estado reactivo:
 * vive en una variable del cierre, fuera de Pinia, de las devtools y de cualquier almacenamiento
 * del navegador (AM-705).
 */
export const useSessionStore = defineStore("session", () => {
  const status = ref<SessionStatus>("unknown");
  const userId = ref<string | null>(null);
  const rawName = ref<string | null>(null);
  const expiresAt = ref<string | null>(null);
  let csrf: string | undefined;
  let inflight: Promise<void> | undefined;

  /** Nombre que da el IdP: texto no confiable, siempre acotado y mostrado como texto. */
  const displayName = computed(() => {
    const name = rawName.value?.trim();
    // El servidor ya sustituye los caracteres peligrosos al iniciar sesión; el navegador es la
    // última barrera y hace visibles bidi, ancho cero y ANSI como en el resto de campos (F7-21).
    return name === undefined || name === ""
      ? FALLBACK_NAME
      : visibleText(name.slice(0, MAX_NAME), MAX_NAME);
  });
  const authenticated = computed(() => status.value === "authenticated");

  function csrfToken(): string | undefined {
    return csrf;
  }

  function accept(session: Session): void {
    csrf = session.csrf_token;
    userId.value = session.user.id;
    rawName.value = session.user.display_name;
    expiresAt.value = session.expires_at;
    status.value = "authenticated";
  }

  function expire(): void {
    csrf = undefined;
    userId.value = null;
    rawName.value = null;
    expiresAt.value = null;
    if (status.value !== "loading") status.value = "anonymous";
  }

  async function fetchSession(api: Pick<Api, "session">): Promise<void> {
    status.value = "loading";
    try {
      accept(await api.session());
    } catch (error) {
      csrf = undefined;
      userId.value = null;
      rawName.value = null;
      status.value = error instanceof ApiError && error.unauthorized ? "anonymous" : "error";
    }
  }

  /** Pide la sesión una sola vez aunque haya varias navegaciones a la vez. */
  function ensureLoaded(api: Pick<Api, "session">): Promise<void> {
    if (status.value !== "unknown" && status.value !== "error") return Promise.resolve();
    inflight ??= fetchSession(api).finally(() => {
      inflight = undefined;
    });
    return inflight;
  }

  async function signOut(api: Pick<Api, "logout">): Promise<void> {
    try {
      await api.logout();
    } catch (error) {
      // Si el servidor ya no reconoce la sesión, el resultado buscado se ha cumplido.
      if (!(error instanceof ApiError && error.unauthorized)) throw error;
    }
    status.value = "anonymous";
    expire();
  }

  return { status, userId, expiresAt, displayName, authenticated, csrfToken, ensureLoaded, expire, signOut };
});
