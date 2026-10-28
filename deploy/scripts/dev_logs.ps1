# Vuelca estado y logs de los servicios a deploy\dev-logs.txt (solo desarrollo).
$c = @("-f","docker-compose.yml","-f","docker-compose.dev.yml","-f","docker-compose.build.yml",
       "--profile","dev","--env-file","deploy/images.dev.env","--env-file","deploy/.env")
$out = "deploy\dev-logs.txt"
& { docker compose @c ps -a; foreach ($s in "scan-runner","api","worker","scheduler","egress-proxy","frontend","keycloak") {
    "`n===== $s ====="; docker compose @c logs --no-color --tail 40 $s } } *>&1 | Out-File -Encoding utf8 $out
Write-Host "Guardado en $out"
