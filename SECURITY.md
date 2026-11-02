# Política de seguridad

NAVE ejecuta herramientas sobre código que no es de fiar, así que cualquier fallo de aislamiento,
autenticación o autorización se trata con prioridad.

## Cómo informar de una vulnerabilidad

Usa el **reporte privado de vulnerabilidades** de GitHub: pestaña *Security* → *Report a
vulnerability*. No abras un issue público.

Incluye, si puedes:

- componente afectado (API, worker, `scan-runner`, contenedores de escaneo, frontend, despliegue);
- pasos para reproducirlo y el impacto que esperas;
- versión o commit.

Respondo en un plazo de 7 días y, si se confirma, se corrige con un test de regresión y se
documenta en el modelo de amenazas.

## Alcance

Dentro: todo el código de este repositorio y la configuración de despliegue que propone
(`docker-compose.yml`, Nginx, runbook de producción con Docker rootless y gVisor).

Fuera: vulnerabilidades de Semgrep, Trivy, gVisor u otras dependencias, que deben comunicarse a sus
proyectos (salvo que NAVE las use de forma insegura), y despliegues que se aparten del runbook, como
el perfil de desarrollo con Docker Desktop, que no es una barrera de seguridad.

## Documentación de seguridad

- [Modelo de amenazas](docs/threat-model.md)
- [Informe ejecutivo de la revisión de seguridad](docs/security/fase7-informe-ejecutivo.md)
- [Revisión detallada](docs/security/fase7-revision.md)
