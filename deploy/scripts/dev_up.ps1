# Despliegue de DESARROLLO en Windows + Docker Desktop. Ejecutar desde la raiz del repo:
#   powershell -ExecutionPolicy Bypass -File deploy\scripts\dev_up.ps1
$ErrorActionPreference = "Stop"
$c = @("-f","docker-compose.yml","-f","docker-compose.dev.yml","-f","docker-compose.build.yml",
       "--profile","dev","--env-file","deploy/images.dev.env","--env-file","deploy/.env")

function Step([string]$name, [scriptblock]$cmd) {
    Write-Host "==> $name"
    & $cmd
    if ($LASTEXITCODE -ne 0) { throw "Fallo en: $name (codigo $LASTEXITCODE)" }
}

docker info *> $null
if ($LASTEXITCODE -ne 0) {
    throw "El motor de Docker no responde. Abre Docker Desktop y espera a que ponga 'Engine running'."
}
Step "Construir imagenes" { docker compose @c --profile build-only build }
# El runner exige imagenes de escaner fijadas por digest: se usa el de las imagenes recien construidas.
foreach ($pair in @(@("NAVE_SEMGREP_IMAGE","nave-semgrep"),@("NAVE_TRIVY_IMAGE","nave-trivy"),@("NAVE_FETCHER_IMAGE","nave-fetcher"))) {
    $id = (docker image inspect "$($pair[1]):dev" --format '{{.Id}}').Trim()
    if ($id -notmatch '^sha256:[0-9a-f]{64}$') { throw "Digest inesperado para $($pair[1]): $id" }
    Set-Item -Path "Env:$($pair[0])" -Value "$($pair[1])@$id"
}
Step "Migraciones" { docker compose @c --profile ops run --rm migrate }
Step "Levantar servicios" { docker compose @c up -d }
Step "Estado" { docker compose @c ps }
Write-Host "Listo: https://localhost  (usuario dev / clave dev). Acepta el aviso del certificado autofirmado."
Write-Host "Si keycloak.localhost no resuelve, anade '127.0.0.1 keycloak.localhost' al fichero hosts."
