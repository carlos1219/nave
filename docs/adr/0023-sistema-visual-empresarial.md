# 0023 — Sistema visual: de «Brutalismo Técnico» a interfaz empresarial moderna

- **Estado:** Aceptado (2026-10-12; decisión del responsable del proyecto tras ver el primer despliegue: «moderno, simple y empresarial, sin aspecto de generado por IA»)
- **Fecha:** 2026-10-12
- **Sección de CLAUDE.md afectada:** §13 (sustituida), §14 (sin cambios)
- **Sustituye en parte a:** ADR 0021 (solo el apartado de tokens visuales)

## Contexto

El §13 original imponía un estilo «brutalismo técnico»: sin sombras, sin bordes redondeados, solo blanco y negro, filas de 24 px. En el primer despliegue real el resultado se percibió como anticuado y poco cuidado, y dificultaba distinguir jerarquías (cabeceras, tarjetas, acciones).

## Decisión

- Sistema visual **claro y oscuro** (según `prefers-color-scheme`), definido solo por tokens CSS en `app/styles/tokens.css`.
- Superficies tipo tarjeta sobre fondo gris muy claro; bordes de 1 px suaves, radios de 6–8 px y una sombra sutil (`--shadow-sm`) únicamente en tarjetas; `--shadow-md` solo en el modal.
- Un único color de acento (azul) para acciones primarias, enlaces y foco; la severidad usa pares texto/fondo propios.
- **La severidad nunca se comunica solo por color:** la insignia lleva siempre la etiqueta textual (`CRITICAL`, `HIGH`, …) y un punto.
- Tipografía: Inter con retroceso a la fuente del sistema (Segoe UI, etc.); monoespaciada solo para datos técnicos (commits, rutas, reglas, logs). Sin fuentes externas por la CSP.
- Sin emojis, iconos decorativos, ilustraciones, degradados, desenfoques ni animaciones decorativas. Los logs y snippets siguen renderizándose como **texto plano**.
- Los colores (hex, rgb) solo se declaran en `tokens.css`; el resto de hojas usa tokens. `border-radius` y `box-shadow` solo con tokens.
- Se mantienen: clases BEM-like propias y `<style scoped>`; prohibido Tailwind o clases utilitarias.

## Verificación

- `tests/style/css.test.ts`: prohíbe degradados, `text-shadow`, animaciones, `backdrop-filter`, `!important`; exige radios y sombras por token y colores solo en `tokens.css`.
- `tests/style/contrast.test.ts`: contraste WCAG AA (4,5:1) en tema claro y oscuro para texto, enlaces, botón primario, errores, logs y las cinco severidades.
- Capturas con Chromium real (claro y oscuro) en todas las rutas: 0 violaciones de CSP.

## Consecuencias

- Se pierde la máxima densidad de datos (filas de 44 px en lugar de 24 px); se gana legibilidad. Si hiciera falta más densidad en tablas grandes, se añade un modo compacto por token (`--row-h`).
- La CSP no cambia.
