# 0026 — Denegaciones de no miembros: aviso estructurado, no audit log

- **Estado:** Aceptado (2026-10-25; fase 7, hallazgo F7-16; decisiones delegadas por el responsable del proyecto: «lo más seguro y lo que menos problemas dé»)
- **Fecha:** 2026-10-25
- **Sección de CLAUDE.md afectada:** §12 (auditoría de accesos denegados)
- **Desarrolla:** ADR 0004 (organizaciones) y ADR 0017 (404 ajeno/inexistente idénticos)

## Contexto

El `Authorizer` escribía una fila `access.denied` en el audit log por **cada** acceso denegado, también cuando el actor no pertenece a la organización (que recibe 404 para no revelar su existencia, AM-106). En la fase 7 se comprobó con el sistema real que:

- Cualquier usuario autenticado puede generar esas filas sin otro límite que el de peticiones por usuario (cientos por minuto, ×4 procesos), y con **identificadores que no existen**: se encontraron 24 filas apuntando a organizaciones inexistentes.
- Las filas se atribuyen a la organización de destino: un usuario de otra organización podía llenar y ensuciar el rastro de auditoría de una ajena.
- El audit log es una cadena con hash y de solo añadir: no se puede podar, así que crece de forma permanente y diluye los eventos legítimos.

## Decisión

- Un acceso denegado a un **miembro** de la organización (rol insuficiente, 403) se sigue auditando en la base de datos, como hasta ahora. Solo pueden provocarlo usuarios con acceso legítimo a esa organización.
- Un acceso de un **no miembro** (404) deja de escribir en el audit log. Se emite un aviso estructurado (`nave.authz`, nivel `WARNING`, mensaje «access denied to a non-member») con `action=access.denied`, `user_id`, `organization_id`, `target_type` y `target_id`. Es la fuente para alertas (picos por usuario) y no afecta a la respuesta, que sigue siendo idéntica a la de un recurso inexistente.
- Los campos nuevos del log (`action`, `user_id`, `organization_id`, `target_type`, `target_id`) se añaden a la lista cerrada del formateador. Son identificadores internos validados como UUID, no datos del cliente en texto libre.

## Alternativas descartadas

- **Seguir auditando todo con un tope por actor:** exigiría estado compartido entre los procesos de la API (hoy el limitador es por proceso) y seguiría escribiendo basura en la cadena.
- **Auditar solo si la organización existe:** reduce el ruido pero un usuario puede seguir llenando el rastro de una organización real.
- **Mover todo al log:** perdería la trazabilidad inmutable de los intentos de un miembro de excederse, que es justo lo que pide §12.

## Superficie de ataque y mitigaciones

- Cierra un vector de denegación de servicio por crecimiento de la base de datos y de contaminación del rastro de otra organización.
- La señal de sondeo no se pierde: queda en los logs de la API, que deben alertar por la tasa de avisos por usuario.
- Residual: los logs no son inmutables como el audit log; la retención y la inmutabilidad de los logs dependen del despliegue (RR-30).

## Verificación

- `backend/tests/unit/application/test_services.py`: un no miembro recibe `NotFoundError`, deja un aviso con los campos esperados y **no** escribe en el audit log, ni siquiera tras 20 sondeos de organizaciones inexistentes; un miembro con rol insuficiente sí se audita.
- `backend/tests/security/test_tenant_isolation_api.py` y `backend/tests/integration/persistence/test_use_cases_on_postgres.py` (PostgreSQL real) actualizados al nuevo comportamiento.
