import { inject } from "vue";
import type { App, InjectionKey } from "vue";

import type { Api } from "./endpoints";

const API_KEY: InjectionKey<Api> = Symbol("nave-api");

export function installApi(app: App, api: Api): void {
  app.provide(API_KEY, api);
}

/** El cliente se obtiene siempre por inyección: ningún componente construye el suyo. */
export function useApi(): Api {
  const api = inject(API_KEY);
  if (api === undefined) throw new Error("el cliente API no está disponible en este árbol");
  return api;
}
