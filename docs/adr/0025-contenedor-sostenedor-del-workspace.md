# 0025 — Contenedor sostenedor del workspace (volumen tmpfs compartido entre fetch y escaneo)

- **Estado:** Aceptado (2026-10-18; fase 7, hallazgo F7-05; decisiones delegadas por el responsable del proyecto: «lo más seguro y lo que menos problemas dé»)
- **Fecha:** 2026-10-18
- **Sección de CLAUDE.md afectada:** §8 (workspace efímero y aislamiento)
- **Desarrolla:** ADR 0012 (protocolo del runner; workspace tmpfs)

## Contexto

El ADR 0012 define un workspace como volumen `tmpfs` (driver `local`, `type=tmpfs`): el contenedor de fetch (con red solo hacia el proxy) clona en él y el contenedor de escaneo (sin red) lo monta de solo lectura. En la primera ejecución real contra el motor se comprobó que **ese traspaso no funciona**: un volumen `tmpfs` del driver `local` se monta con el primer contenedor que lo usa y se desmonta, vaciándose, cuando termina el último. Lo que escribe el fetch desaparece al acabar y el escaneo ve un workspace vacío.

Verificado con Docker 29.8.2: un contenedor escribe en el volumen, otro contenedor lo monta después y no ve el fichero; con un contenedor adicional que mantiene el volumen montado, el contenido persiste entre contenedores y desaparece al retirarlo. Con el código anterior Semgrep terminaba con «Invalid scanning root: repo» (código 2) y el runner respondía `scanner_failed`: ningún escaneo podía completarse. Falla cerrado (no hubo falsos PASS), pero la plataforma no podía analizar nada. Los tests unitarios usaban un motor falso y no podían detectarlo.

## Decisión

Cada workspace tiene un **contenedor sostenedor** (`nave-hold-<run_id>`) que mantiene el volumen montado desde `fetch` hasta `release`:

- Se arranca justo después de crear el volumen y antes del fetch (`start_holder`); si no arranca, el fetch falla como transitorio y el workspace se descarta.
- Plantilla fija (`hold_spec`): imagen del fetch (ya fijada por digest), orden `sleep 86400`, **sin red**, rootfs de solo lectura, `cap_drop ALL`, `no-new-privileges`, usuario 65532, 16 MiB de memoria, 8 PID, 0,1 CPU, sin variables de entorno ni tmpfs, y solo el volumen de su ejecución. No ejecuta nada del repositorio.
- Etiquetas `nave.managed`, `nave.run` y `nave.role=hold`: el barrido de huérfanos lo reconoce (nombre `nave-hold-<id>`) y lo retira por antigüedad como al resto.
- `release` y el descarte tras un fallo retiran el sostenedor **el último**, justo antes del volumen.
- Un escaneo solo se acepta si el workspace está **listo**: el volumen existe y el sostenedor sigue en marcha. Si el sostenedor murió, el contenido ya se perdió y la respuesta es `unknown_workspace` (404), no un escaneo sobre un árbol vacío (que daría un falso PASS).

El tope de vida del sostenedor (24 h) es mayor que la edad máxima de huérfanos (6 h por defecto): el barrido lo retira antes y, si algo fallara, el contenedor termina solo y el workspace se pierde en lugar de quedar para siempre.

## Alternativas descartadas

- **Volumen con disco (driver `local` sin tmpfs):** persistiría sin sostenedor, pero los datos del repositorio acabarían en disco, sin tope de tamaño del motor y sin `noexec`; el tmpfs se borra solo y acota memoria.
- **Un único contenedor que clone y escanee:** el escáner tendría red y el token de clonado, que es justo lo que ADR 0002 evita.
- **Bind mount de un directorio del host:** exige que el runner conozca rutas del host y complica el motor rootless.

## Superficie de ataque y mitigaciones

- El sostenedor no tiene red, ni secretos, ni acceso al repositorio más allá de montar el mismo volumen; su orden es fija. Un compromiso del escáner o del fetch no lo alcanza (contenedores distintos, sin socket del motor).
- Más contenedores vivos por ejecución (3 con el fetch, 2 con el escaneo): mismos límites de ejecuciones simultáneas del runner; consumo mínimo (16 MiB).
- Si el sostenedor desaparece a mitad de una ejecución, el escaneo se rechaza en vez de analizar vacío.

## Verificación

- `runner/tests/test_sandbox_spec.py::TestHoldContainer`, `test_service.py` (arranca el sostenedor antes del fetch; si falla no se clona; un workspace sin sostenedor es desconocido) y `test_engine.py` (arranque desacoplado, `workspace_ready`, retirada con el workspace, reconocimiento por el barrido).
- Con el stack real (Docker Desktop, perfil `dev`): workspace preparado con el propio código del runner, repositorio hostil en él y escaneo de Semgrep firmado a través del protocolo: respuesta 200 con los resultados esperados; `release` retira sostenedor y volumen (ningún recurso con la etiqueta de la ejecución). Plazo de 1 s → 504 y tope de salida de 100 bytes → 413, sin contenedores residuales, y un escaneo normal posterior funciona.

## Límites conocidos

- La orquestación completa fetch → sostenedor → escaneo contra GitHub real no se ha ejecutado (falta GitHub App); el paso de fetch se probó solo por unidades y el traspaso del volumen con el motor real.
- En desarrollo el motor es Docker Desktop (runc, sin gVisor): el comportamiento del driver `local` coincide con el de dockerd en Linux, pero debe repetirse la prueba en el motor rootless de producción.
