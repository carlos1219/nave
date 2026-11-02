# Runbook — Host de producción con Docker rootless y gVisor

Procedimiento verificado el 2026-11-02 en un host Linux simulado (WSL2, Ubuntu 24.04 LTS,
Docker Engine 29.9.2 rootless, gVisor `release-20261026.0`): la pila completa de NAVE arrancó
sana y un escaneo real de un repositorio de GitHub terminó con los contenedores de clonado,
sostenedor y escaneo bajo `runsc`. Resuelve las condiciones RR-20 y RR-29 del informe de la
fase 7 (`docs/security/fase7-informe-ejecutivo.md`).

## 1. Paquetes (como root)

Repositorios oficiales con clave GPG verificada:

```bash
apt-get install -y ca-certificates curl gnupg uidmap dbus-user-session slirp4netns fuse-overlayfs iptables
# Docker: https://download.docker.com/linux/ubuntu  (docker-ce, docker-ce-rootless-extras,
#         docker-compose-plugin, docker-buildx-plugin)
# gVisor: https://storage.googleapis.com/gvisor/releases  (paquete runsc)
systemctl disable --now docker.service docker.socket   # no se usa el daemon de root
useradd -m -s /bin/bash nave && loginctl enable-linger nave
echo 'nave:100000:65536' >> /etc/subuid; echo 'nave:100000:65536' >> /etc/subgid
echo 'net.ipv4.ip_unprivileged_port_start=80' > /etc/sysctl.d/90-nave-ports.conf && sysctl -p /etc/sysctl.d/90-nave-ports.conf
```

El `sysctl` permite al usuario sin privilegios publicar los puertos 80 y 443 del frontend.

## 2. Docker rootless del usuario `nave`

```bash
dockerd-rootless-setuptool.sh install
```

`~/.config/docker/daemon.json`:

```json
{"runtimes": {"runsc": {"path": "/usr/bin/runsc", "runtimeArgs": ["--ignore-cgroups"]}}}
```

`~/.config/systemd/user/docker.service.d/override.conf` (RR-29, ver §5):

```ini
[Service]
Environment="DOCKERD_ROOTLESS_ROOTLESSKIT_NET=slirp4netns"
Environment="DOCKERD_ROOTLESS_ROOTLESSKIT_PORT_DRIVER=slirp4netns"
```

```bash
systemctl --user daemon-reload && systemctl --user restart docker
docker run --rm --runtime=runsc alpine uname -r      # debe mostrar «...-gvisor»
```

## 3. Variables de NAVE (`deploy/.env`)

| Variable | Valor | Por qué |
|---|---|---|
| `NAVE_ENGINE_SOCKET` | `/run/user/<uid>/docker.sock` | Socket del motor rootless |
| `NAVE_ENGINE_SOCKET_GID` | GID del socket **visto desde un contenedor** | En rootless no coincide con el del host. Obtenerlo con `docker run --rm -v /run/user/<uid>/docker.sock:/s alpine stat -c %g /s` (en la prueba: 989) |
| `NAVE_RUNNER_RUNTIME` | `runsc` | Contenedores efímeros bajo gVisor (ADR 0002) |
| `NAVE_EGRESS_PROXY_IP` / `NAVE_EGRESS_SUBNET` | por defecto `172.31.250.10` / `172.31.250.0/24` | Cambiar solo si la subred choca con otra del host (F7-35) |

## 4. Secretos

Compose sin swarm monta los ficheros de `deploy/secrets` tal cual. En rootless el usuario de cada
servicio (101, 999, 65532...) es un UID del host distinto del dueño: los ficheros deben poder
leerse (`0644`) dentro de un directorio `0700` del usuario `nave`, que es lo que impide que otros
usuarios del host los vean. Verificado: todos los servicios arrancan sanos.

## 5. Comprobaciones tras desplegar

1. **Aislamiento (RR-20):** lanzar un escaneo y, mientras corre, `docker inspect <nave-fetch-…|nave-scan-…|nave-hold-…> --format '{{.HostConfig.Runtime}}'` debe dar `runsc`.
2. **IP del cliente (RR-29):** dos clientes distintos deben aparecer con IP distinta en `docker logs nave-frontend-1`. Con el controlador de puertos por defecto (`builtin`) todos aparecían como la pasarela `172.20.0.1`; con `slirp4netns`, el cliente de Windows aparece con su IP real.
3. **Salida (ADR 0007):** `docker logs nave-egress-proxy-1` solo debe mostrar `github.com` y `api.github.com`.

## 6. Problemas encontrados en la prueba

- **F7-35 — DNS bajo gVisor:** con `runsc`, el contenedor de clonado no resolvía `egress-proxy` («Could not contact DNS servers»): gVisor usa su propia pila de red y no alcanza el DNS interno de Docker. El runner llega al proxy por IP fija en la red `egress` (`NAVE_RUNNER_EGRESS_PROXY_URL`). Los contenedores de escaneo no tienen red y GitHub lo resuelve el propio proxy, así que no hace falta ningún otro DNS.
- **GID del socket:** con `NAVE_ENGINE_SOCKET_GID=0` (válido en Docker Desktop) el runner recibía «permission denied» y todos los escaneos fallaban con `fetch_unavailable`.
- **IP de cliente:** ver §5.2.

## 7. Limitaciones de la prueba

- WSL2 no es un servidor: el kernel es el de Microsoft y la red pasa por la VM de WSL. El comportamiento de rootless, gVisor y RootlessKit es el mismo que en un Linux nativo, pero conviene repetir §5 en el host real.
- `--ignore-cgroups` delega los límites de CPU y memoria en el contenedor exterior que crea Docker; no se ha medido su efecto bajo carga.
- No se ha probado un servidor con varias interfaces ni un balanceador delante de Nginx.
