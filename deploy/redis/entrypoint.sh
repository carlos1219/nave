#!/bin/sh
# Redis sin persistencia (§15: no es almacén de estado). Las contraseñas se leen de los
# secretos y se escriben como HASH en una configuración en tmpfs (0600): no aparecen en los
# argumentos del proceso.
#
# Un usuario por servicio (RR-31). Las listas salen de observar con MONITOR un ciclo
# completo (escaneo, cancelación, webhook de instalación y tareas periódicas), más los
# caminos poco frecuentes del worker (restauración de mensajes sin confirmar):
# - nave-api y nave-scheduler solo publican: LPUSH a sus colas, LLEN (tope de cola), SADD/
#   SMEMBERS en las tablas de enlaces de kombu. La API además difunde la revocación por el
#   canal pidbox (`revoke`, CLAUDE.md §10). Ninguno puede consumir, borrar ni ejecutar Lua.
# - nave-worker consume y gestiona `unacked`. EVAL/EVALSHA/SCRIPT no se pueden quitar: el
#   `Lock` de redis-py (mutex de kombu) libera con un script Lua y sin ellos el worker cae
#   con «Unrecoverable error: NoPermissionError» (F7-18). Sin comandos de administración,
#   de vaciado masivo ni funciones persistentes.
#   PSUBSCRIBE compara el patrón de forma literal con los permitidos: por eso
#   `/0.celeryev/worker.*` aparece tal cual además de `/0.celeryev/*` (que cubre PUBLISH).
# - health: solo PING, para el healthcheck del contenedor; no tiene acceso a claves.
# - default desactivado.
set -eu
umask 077
hash_of() { printf %s "$(cat "/run/secrets/$1")" | sha256sum | cut -d' ' -f1; }
api=$(hash_of redis_api_password)
worker=$(hash_of redis_worker_password)
scheduler=$(hash_of redis_scheduler_password)
base="-@all +ping +hello +client|setinfo +client|setname +select +multi +exec +discard"
cat > /tmp/redis.conf <<CONF
bind 0.0.0.0
port 6379
protected-mode yes
save ""
appendonly no
dir /tmp
maxmemory 256mb
maxmemory-policy noeviction
user default off resetkeys resetchannels -@all
user health on nopass resetkeys resetchannels -@all +ping +hello
user nave-api on #${api} resetkeys resetchannels ${base} +lpush +llen +sadd +smembers +publish ~scan* ~report* ~maintenance* ~_kombu.binding.* &/0.celery.pidbox
user nave-scheduler on #${scheduler} resetkeys resetchannels ${base} +lpush +llen +sadd +smembers ~maintenance* ~_kombu.binding.*
user nave-worker on #${worker} resetkeys resetchannels +@all -@admin -@dangerous -function -fcall -fcall_ro ~scan* ~report* ~maintenance* ~_kombu.binding.* ~unacked* ~*pidbox* ~celeryev.* &/0.celery.pidbox &/0.celeryev/* &/0.celeryev/worker.*
CONF
exec redis-server /tmp/redis.conf
