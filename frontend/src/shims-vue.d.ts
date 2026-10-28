// Para el servicio de TypeScript de ESLint; `vue-tsc` resuelve los .vue por sí mismo.
declare module "*.vue" {
  import type { Component } from "vue";
  const component: Component;
  export default component;
}
