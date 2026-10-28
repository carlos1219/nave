# 0013 — El token de instalación de GitHub es un valor opaco

- **Estado:** Aceptado (2026-10-12, confirmado por el responsable del proyecto)
- **Fecha:** 2026-10-12
- **Sección de CLAUDE.md afectada:** §8, §9
- **Enmienda:** ADR 0012 (validación del token en el protocolo del runner)

## Contexto

ADR 0012 valida el token de instalación con `[A-Za-z0-9_-]{20,255}` en tres sitios: `protocol.py` del backend, `schema.py` del runner y `nave_fetch.py` del fetcher. Eso asume el formato histórico `ghs_` + 36 caracteres.

GitHub cambia el formato a `ghs_<APPID>_<JWT>`: unos 520 caracteres, con puntos, y GitHub avisa de que su longitud varía y de que los clientes no deben interpretarlo. Según el calendario anunciado, llega a todas las GitHub Apps entre mediados de mayo y finales de junio de 2026. Existe una cabecera para pedir el formato antiguo por petición (`X-GitHub-Stateless-S2S-Token: disabled`), pero GitHub ha anunciado que dejará de respetarla.

Con el formato nuevo el paso `fetch` fallaría en producción: el cliente rechaza el token (puntos y longitud). Los tests no lo detectan porque todos usan el formato antiguo. Además:

- `nave_fetch.py` lee el token con `readline(512)`: un token de ~520 caracteres se truncaría.
- La redacción de logs (`logging.py`) cubre `gh[pousr]_[A-Za-z0-9]{20,}`; en el formato nuevo el `_` tras el ID de la App corta la coincidencia y el token se registraría en claro (AM-306).

## Opciones consideradas

1. **Pedir el formato antiguo con la cabecera de override.** Descartada: es temporal y GitHub ha anunciado que dejará de respetarla.
2. **Ensanchar el patrón a una longitud y un juego de caracteres fijos del formato nuevo.** Frágil: GitHub dice que el formato puede cambiar.
3. **Tratar el token como opaco** y validar solo lo que importa para la seguridad del canal: ASCII imprimible sin espacios, un juego de caracteres seguro para una cabecera HTTP de Basic y para stdin, y un tope de longitud holgado.

## Decisión

Opción 3.

- Patrón único `[A-Za-z0-9._-]{20,1024}` (ASCII) en backend, runner y fetcher. Tres copias con un único valor de referencia en `contracts/runner-protocol-vectors.json`, que ambas implementaciones verifican.
- `nave_fetch.py` lee con `readline(1100)`.
- `MAX_REQUEST_BYTES` (4096) se mantiene: el cuerpo del paso `fetch` con un token de 1024 caracteres cabe con margen.
- La redacción de logs pasa a cubrir `ghs_` con cualquier sufijo hasta el primer carácter que no pertenezca al patrón del token, y los tests incluyen ambos formatos.
- Los tests de fetch, del runner y de redacción usan tokens de los dos formatos.
- No se usa la cabecera de override.

## Superficie de ataque y mitigaciones

El token sigue viajando por stdin y nunca por argumentos, entorno ni fichero (ADR 0012). El juego de caracteres sigue excluyendo espacios, saltos de línea y comillas, así que no cambia la superficie de inyección en la cabecera `Authorization` que construye git. Ver AM-306 y AM-506.

## Consecuencias

- Corrección previa a la fase 4 (componente C0), porque afecta a una fase ya cerrada.
- El proveedor de tokens (ADR 0014) no puede asumir longitud ni estructura del token.

## Verificación contra la documentación (2026-10-12)

- El anuncio oficial de formatos de token de GitHub (marzo de 2021) fija el juego `[A-Za-z0-9_]`, prefijos `ghs_` (servidor a servidor) y similares, y pide soportar hasta 255 caracteres. El patrón elegido (`[A-Za-z0-9._-]{20,1024}`) lo cubre con margen.
- `X-GitHub-Api-Version`: 2022-11-28 sigue soportada hasta el 10-03-2028; 2026-03-10 es la última. Ningún cambio incompatible de 2026-03-10 afecta a los endpoints y campos que usa NAVE (tokens de instalación, listado de repositorios de la instalación, check runs, SARIF de subida, tarball). Se mantiene 2022-11-28 y se revisará la lista antes de migrar.
