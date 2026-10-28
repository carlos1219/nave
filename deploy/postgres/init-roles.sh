#!/bin/sh
# (sin `set -e`: el entrypoint de la imagen puede cargar este fichero con `source`.)
# Primer arranque de PostgreSQL: crea los tres roles sin privilegios (modelo-de-datos §3) y
# reparte la propiedad. Las contraseñas llegan de Docker secrets; `\set x `cat ...`` las lee
# dentro de psql, así que no aparecen en argumentos ni en el entorno.
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres <<'SQL'
\set migrator_pw `cat /run/secrets/db_migrator_password`
\set app_pw `cat /run/secrets/db_app_password`
\set maintenance_pw `cat /run/secrets/db_maintenance_password`

CREATE ROLE nave_migrator LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION PASSWORD :'migrator_pw';
CREATE ROLE nave_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION PASSWORD :'app_pw';
CREATE ROLE nave_maintenance LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION PASSWORD :'maintenance_pw';

REVOKE ALL ON DATABASE nave FROM PUBLIC;
ALTER DATABASE nave OWNER TO nave_migrator;
GRANT CONNECT ON DATABASE nave TO nave_app, nave_maintenance;
SQL
[ "$?" -eq 0 ] || exit 1

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname nave <<'SQL'
ALTER SCHEMA public OWNER TO nave_migrator;
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO nave_app, nave_maintenance;
SQL
