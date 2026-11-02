# NAVE en cinco minutos

## La idea

NAVE analiza automáticamente el código de cada push y pull request de GitHub con Semgrep y Trivy,
decide con una política si la PR puede entrar y lo publica en GitHub. Lo difícil no es lanzar
escáneres: es hacerlo **sobre código que no es de fiar** sin que el propio escáner se convierta en
la puerta de entrada.

## Cifras

| | |
|---|---|
| Arquitectura | Hexagonal, 8 servicios en Docker Compose, 34 decisiones documentadas (ADR) |
| Tests | ~2.900 entre backend, runner, fetcher, despliegue y frontend; cobertura del backend 97 % |
| Seguridad | Modelo de amenazas STRIDE y revisión como atacante con 36 hallazgos corregidos o aceptados con justificación, cada corrección con su test |
| Verificación real | Escaneo de un repositorio real de GitHub de extremo a extremo; despliegue de producción simulado con Docker rootless + gVisor; CI en GitHub Actions en verde |
| Cadena de suministro | Release `v1.0.0` con 7 imágenes firmadas con cosign sin clave, SBOM CycloneDX como atestación y OpenSSF Scorecard publicado |

## Cinco historias que vale la pena contar

1. **El escáner que se podía engañar con tamaño (F7-02).** Semgrep ignoraba en silencio los ficheros
   de más de 2 MB: bastaba rellenar un fichero para esconder un `eval()` al quality gate. Ahora cada
   fichero omitido aparece como hallazgo y el veredicto deja de ser «aprobado» (ADR 0024).
2. **Ningún escaneo podía terminar (F7-05).** Cada pieza pasaba sus tests, pero el código clonado
   nunca llegaba al contenedor de escaneo porque el volumen temporal se vaciaba al desmontarse. Solo
   apareció al ejecutar la cadena completa. La solución fue un contenedor sostenedor sin red (ADR 0025).
3. **Una escalada de privilegios en una función nueva (F7-31).** El rol de administrador de
   plataforma, pensado para no ver datos de nadie, podía darse a sí mismo el rol de administrador
   en cualquier organización. Lo destapó una revisión del delta tras un rediseño.
4. **El bit que Windows esconde (F7-34).** Las imágenes de los escáneres construidas desde el
   repositorio no podían ejecutar su envoltorio: Git en Windows no guarda el permiso de ejecución y
   Docker Desktop lo añade al construir en local. Lo encontró un contrato de CI nuevo en su primera
   ejecución.
5. **gVisor y el DNS (F7-35).** Con el aislamiento de producción, el contenedor de clonado no
   resolvía el proxy de salida: gVisor trae su propia pila de red y no ve el DNS interno de Docker.
   Invisible en desarrollo, apareció al montar la producción simulada.

La lección común: **los tests unitarios no bastan**. Los fallos graves aparecieron al ejecutar las
piezas juntas contra binarios, servicios y entornos reales.

## Cómo trabajé con IA

Si sale en la entrevista, la respuesta corta: usé Claude Code como asistente bajo un contrato de
ingeniería (`CLAUDE.md`) con reglas de seguridad no negociables. Yo marqué el alcance, elegí entre
las opciones que proponía y decidí qué riesgos aceptar (34 ADR). Nada se dio por bueno sin tests,
CI y pruebas contra sistemas reales, y la revisión de seguridad trató como sospechoso también el
código que había generado el asistente: ahí salieron F7-05, F7-31, F7-34 y F7-35.

## Guion de demo

1. Abrir una PR con código vulnerable en el repositorio de prueba.
2. Enseñar el check y el comentario de NAVE en la PR: hallazgos nuevos por severidad y veredicto FAIL.
3. En la consola: estadísticas de la organización, cola de triage y detalle de un hallazgo.
4. Marcar uno como falso positivo con justificación y enseñar el registro de auditoría.
5. Enseñar el diagrama del README y explicar por qué el escáner corre bajo gVisor sin red.

## Siguientes pasos

- Repetir el runbook de producción en el servidor definitivo con las imágenes firmadas de `v1.0.0`.
- Añadir un tercer escáner (Gitleaks) para demostrar que no hace falta tocar el núcleo.
