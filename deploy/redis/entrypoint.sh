#!/bin/sh
# Redis sin persistencia (§15: no es almacén de estado). La contraseña se lee del secreto y
# se escribe como HASH en una configuración en tmpfs (0600): no aparece en los argumentos del
# proceso. Sin comandos de administración (CONFIG, SHUTDOWN, DEBUG...), sin vaciado masivo y
# sin funciones persistentes (FUNCTION, FCALL: nadie las usa y sobreviven a la conexión).
# EVAL/EVALSHA/SCRIPT LOAD **no** se pueden denegar: el `Lock` de redis-py (mutex de kombu
# para restaurar mensajes sin confirmar) libera con un script Lua, y sin ellos el worker cae
# con «Unrecoverable error: NoPermissionError» (comprobado en la fase 7, F7-18).
set -eu
umask 077
hash=$(printf %s "$(cat /run/secrets/redis_password)" | sha256sum | cut -d' ' -f1)
cat > /tmp/redis.conf <<CONF
bind 0.0.0.0
port 6379
protected-mode yes
save ""
appendonly no
dir /tmp
maxmemory 256mb
maxmemory-policy noeviction
user default on #${hash} ~* &* +@all -@admin -function -fcall -fcall_ro -flushall -flushdb
CONF
exec redis-server /tmp/redis.conf
