import { createApp, effectScope } from "vue";
import type { EffectScope } from "vue";

import { installApi } from "@/shared/api";
import type { Api } from "@/shared/api";

/** Ejecuta `fn` dentro de un scope con un cliente API falso inyectado. */
export function withApi<T>(fake: Partial<Record<keyof Api, unknown>>, fn: () => T): { result: T; scope: EffectScope } {
  const app = createApp({});
  installApi(app, fake as unknown as Api);
  const scope = effectScope();
  const result = app.runWithContext(() => scope.run(fn) as T);
  return { result, scope };
}
