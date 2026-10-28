# Carga (o retira con -Remove) los datos de demostracion en la base local. Solo desarrollo.
#   powershell -ExecutionPolicy Bypass -File deploy\scripts\dev_demo.ps1 [-Remove]
param([switch]$Remove)
$ErrorActionPreference = "Stop"
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$c = @("-f","docker-compose.yml","-f","docker-compose.dev.yml","-f","docker-compose.build.yml",
       "--profile","dev","--env-file","deploy/images.dev.env","--env-file","deploy/.env")
$file = if ($Remove) { "deploy\dev\demo_remove.sql" } else { "deploy\dev\demo_data.sql" }
Get-Content -Raw -Encoding UTF8 $file | docker compose @c exec -T postgres psql -U nave_admin -d nave -v ON_ERROR_STOP=1
if ($LASTEXITCODE -ne 0) { throw "Fallo al aplicar $file (codigo $LASTEXITCODE)" }
Write-Host "Hecho. Recarga https://localhost"
