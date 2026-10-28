# 0028 — Vistas de organización: estadísticas, actividad, reglas, riesgos, dependencias, política y auditoría

- **Estado:** Aceptado (2026-10-26; petición del responsable del proyecto: «la aplicación se ve vacía y sin uso»)
- **Fecha:** 2026-10-26
- **Sección de CLAUDE.md afectada:** §5.2 (puerto nuevo), §11 (endpoints), §12 (auditoría visible)

## Contexto

La interfaz solo permitía navegar organización → repositorio → escaneo → hallazgo. No había forma de ver el estado global de una organización, la evolución en el tiempo, qué reglas generan más ruido, qué riesgos aceptados están a punto de caducar, qué dependencias son vulnerables, qué política se aplica ni quién hizo qué.

## Decisión

- **Puerto `InsightsQueries`** (en `application/ports/insights.py`), expuesto como `UnitOfWork.insights`: consultas de **solo lectura**, agregadas en PostgreSQL, filtradas por organización y con tope de filas. Ninguna autoriza: lo hace el caso de uso antes (CLAUDE.md §12).
- **Siete casos de uso** en `application/use_cases/insights.py`, todos con autorización por organización:
  | Endpoint (`/api/v1/organizations/{id}/…`) | Rol mínimo | Contenido |
  |---|---|---|
  | `stats` | VIEWER | abiertos por severidad, escáner y repositorio; 10 reglas con más abiertos; tiempo medio de corrección; fuera de plazo según la política activa; serie de 30 días |
  | `scans` | VIEWER | escaneos en curso y los 100 más recientes de todos los repositorios |
  | `accepted-risks` | VIEWER | riesgos aceptados, el que caduca antes primero, con la última justificación |
  | `rules` | VIEWER | reglas que han producido hallazgos (hasta 500) |
  | `dependencies` | VIEWER | hallazgos de categoría `DEPENDENCY` (SCA de Trivy), abiertos primero |
  | `policy` | VIEWER | versiones de la política de quality gates |
  | `audit-log` | **ADMIN** | entradas del audit log de la organización, paginadas por id |
- **Serie temporal** (`application/services/open_trend.py`, función pura): para cada día, la suma del último recuento de abiertos que dejó un escaneo completado de la rama por defecto de cada repositorio. Se toma además el último escaneo anterior a la ventana como punto de partida.
- **Tiempo de corrección:** días entre la primera y la última detección de los hallazgos `FIXED` (el escaneo siguiente ya no los vio). Es una aproximación por defecto y así se rotula.
- **«Catálogo de reglas»:** son las reglas que han producido al menos un hallazgo. El conjunto completo de reglas vive en la imagen del escáner, fuera del alcance de la API.
- **Frontend:** cada organización tiene pestañas (`widgets/organization-shell`); las gráficas son SVG propios (`shared/ui/Charts`, geometría pura en `shared/lib/chart.ts`), sin dependencias nuevas (ADR 0021). La pestaña de auditoría solo se muestra a ADMIN; la autorización real es la del servidor.

## Seguridad

- Aislamiento: los siete endpoints están en `tests/security/test_tenant_isolation_api.py` (ajeno e inexistente responden idéntico 404).
- El audit log solo lo lee un ADMIN de esa organización; un TRIAGER recibe 403 y queda auditado. Los `details` se vuelven a validar al leer (texto corto y seguro).
- Todos los textos (títulos, reglas, justificaciones, nombres) se pintan como texto; las gráficas usan atributos SVG, nunca estilos en línea.

## Consecuencias

- Más consultas agregadas por petición en `stats` (seis); con los índices existentes por `repository_id` y estado son baratas para el volumen esperado. Si creciera mucho, se materializa la serie.
- La serie temporal depende de los resúmenes de escaneo: un repositorio sin escaneos completados en su rama por defecto no aparece.
