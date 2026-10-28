import { createPinia } from "pinia";
import { createApp, watch } from "vue";
import type { App as VueApp } from "vue";
import type { RouterHistory } from "vue-router";

import { useSessionStore } from "@/entities/session";
import { createApi, createHttp, installApi } from "@/shared/api";

import App from "./App.vue";
import { createNaveRouter } from "./router";

export interface AppOptions {
  fetchImpl?: typeof fetch;
  history?: RouterHistory;
}

/** Ensambla la aplicación; es el único sitio que conecta sesión, cliente y router. */
export function createNaveApp(options: AppOptions = {}) {
  const pinia = createPinia();
  const app: VueApp = createApp(App);
  app.use(pinia);
  const session = useSessionStore(pinia);

  const http = createHttp({
    csrfToken: () => session.csrfToken(),
    onUnauthorized: () => {
      session.expire();
    },
    ...(options.fetchImpl && { fetchImpl: options.fetchImpl }),
  });
  const api = createApi(http);
  const router = createNaveRouter(api, options.history);

  // Sesión caducada con la aplicación abierta: se vuelve a la entrada sin conservar nada.
  watch(
    () => session.status,
    (status) => {
      const current = router.currentRoute.value;
      // Mientras la primera navegación no ha terminado decide la guardia del router.
      if (status === "anonymous" && current.matched.length > 0 && current.meta.public !== true) {
        void router.replace({ name: "login" });
      }
    },
  );

  installApi(app, api);
  app.use(router);
  return { app, router, pinia, api };
}
