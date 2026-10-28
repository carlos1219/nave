import type { RouteLocationRaw } from "vue-router";

/** Paso de la ruta de navegación; sin `to` es la página actual o un paso sin enlace. */
export interface Crumb {
  label: string;
  to?: RouteLocationRaw;
}
