# NAVE · frontend

Vue 3 + TypeScript estricto + Feature-Sliced Design. Decisiones en [ADR 0021](../docs/adr/0021-arquitectura-del-frontend.md).

```
npm ci            # versiones exactas del lockfile; no ejecuta scripts de instalación
npm run check     # vue-tsc + ESLint + Vitest
npm run build
npm run api:types # regenera src/shared/api/schema.d.ts desde ../contracts/openapi.json
```

Nota: instala las dependencias en el mismo sistema operativo en el que vayas a ejecutarlas (los binarios de Vite y esbuild son específicos de plataforma).

Reglas que fallan el CI: capas de FSD y API pública por slice, ningún HTML dinámico (`v-html`, `innerHTML`…), nada de `localStorage`/`sessionStorage`, nada de `fetch` fuera de `shared/api`, y estilos que solo usan los tokens del §13 (sin sombras, degradados, animaciones ni `border-radius`).
