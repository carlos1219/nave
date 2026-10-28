# 0027 — Sistema visual: consola de triage densa

- **Estado:** Aceptado (2026-10-26; rediseño completo del frontend pedido por el responsable del proyecto: «que parezca un producto profesional de seguridad, denso, claro y legible para triage»)
- **Fecha:** 2026-10-26
- **Sección de CLAUDE.md afectada:** §13 (tokens y composición; las prohibiciones no cambian)
- **Modifica a:** ADR 0023 (paleta, tipografía, densidad y componentes). Las prohibiciones y la verificación del 0023 siguen vigentes.

## Contexto

El sistema del ADR 0023 resolvió la legibilidad, pero en uso real la interfaz se percibía como una plantilla genérica de SaaS: azul de acento estándar, cabeceras de tabla en mayúsculas espaciadas, títulos de página sin contexto («Escaneo», «Finding»), filas de 44 px y páginas sin ruta de navegación. Para triage hacen falta más filas por pantalla, saber siempre dónde se está y que el veredicto y los recuentos por severidad se lean de un vistazo.

## Decisión

**Tokens** (`app/styles/tokens.css`):

- Acento **petróleo** (`#0b6487` claro / `#5cb8dc` oscuro) en lugar del azul genérico; no se confunde con la severidad `LOW` (índigo) ni con los estados correcto/fallido.
- Fondo gris frío, texto casi negro azulado; severidades recalibradas con pares texto/fondo AA en ambos temas.
- Tokens nuevos: `--warn`/`--warn-bg` (veredicto `NEUTRAL`, avisos de truncado), `--log-line`, `--log-muted`, `--log-error-bg` (registro), `--space-8`, `--fs-2xl`, `--header-h`. `--radius-sm` pasa a 4 px; `--control-h` a 32 px; se retira `--row-h` (sin uso).
- Tipografía: solo fuentes del sistema (Segoe UI Variable/Segoe UI, system-ui…; Cascadia Mono, SF Mono, Menlo, Consolas…). Se quitan «Inter» y «JetBrains Mono» del primer lugar de la pila porque nunca se cargan (la CSP no permite fuentes externas) y prometían un aspecto que no se daba.

**Composición:**

- Cabeceras de tabla, `dt` y leyendas en tipo de frase, sin mayúsculas espaciadas. Las mayúsculas se reservan a los datos que son códigos (`CRITICAL`, `OPEN`, `PASS`), que van en monoespaciada.
- Filas de tabla de ~38 px; cabecera de 34 px.
- Cada página lleva **ruta de navegación** (`PageLayout` con `trail`), título con el dato real (organización, `owner/repo`, commit, título del hallazgo) y una línea de metadatos.
- Componentes nuevos en `shared/ui`: `Panel` (tarjeta con título h2 y nombre accesible) y `StatusTag` (estado con texto + tono). En `entities/scan`: `ScanStatusTag` y `VerdictTag`.
- Escaneo: banda de quality gate con filete del color del veredicto y frase explicativa; matriz de severidad (nuevos/abiertos) con cifras grandes en monoespaciada; registro dentro de un panel con número de líneas.
- Hallazgo: dos columnas (ocurrencia e historial | detalles y decisión de triage); el estado de triage se elige con un grupo de opciones (`radio`) en vez de un desplegable.
- Filtros de hallazgos como fichas: casillas reales dentro de su etiqueta, marcadas con `:has(:checked)`.
- La UI dice «hallazgos» en lugar de «findings»; los identificadores de código y la API no cambian.

## Verificación

- `tests/style/contrast.test.ts` amplía las parejas comprobadas: texto secundario y terciario sobre `--surface-2`, acento sobre `--accent-soft`, correcto, aviso y las variantes del registro.
- `tests/style/css.test.ts` sin cambios: sigue prohibiendo degradados, sombras de texto, animaciones, desenfoques, `!important`, colores fuera de `tokens.css` y radios/sombras fuera de token.
- Tests unitarios nuevos para la ruta de navegación (escapada, sin enlace en el paso actual), `Panel`, `StatusTag`, tonos de estado/veredicto y `formatDuration`.

## Consecuencias

- Más filas por pantalla y contexto permanente (ruta + título con datos reales).
- `:has()` exige navegadores de 2023 en adelante; sin él las fichas siguen funcionando como casillas, solo pierden el resaltado del estado marcado.
- La página de repositorio pide además la lista de repositorios de la organización para mostrar `owner/name` (una petición de lectura más, ya autorizada por la misma membresía).
