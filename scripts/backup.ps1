param([Parameter(Mandatory=$true)][string]$Destination)
$ErrorActionPreference = "Stop"
$target = [IO.Path]::GetFullPath($Destination)
New-Item -ItemType Directory -Force -Path $target | Out-Null
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$bundle = Join-Path $target "nexora-backup-$stamp"
New-Item -ItemType Directory -Path $bundle | Out-Null
docker compose exec -T postgres pg_dump -U platform_admin -d central_monitor -Fc --no-owner --no-acl --file=/tmp/nexora-central-monitor.dump
docker compose cp postgres:/tmp/nexora-central-monitor.dump (Join-Path $bundle "central_monitor.dump")
docker compose exec -T postgres rm -f /tmp/nexora-central-monitor.dump
docker compose exec -T glpi-db sh -lc 'mariadb-dump -uglpi -p"$MARIADB_PASSWORD" glpi > /tmp/nexora-glpi.sql'
docker compose cp glpi-db:/tmp/nexora-glpi.sql (Join-Path $bundle "glpi.sql")
docker compose exec -T glpi-db rm -f /tmp/nexora-glpi.sql
Copy-Item compose.yaml,.env.example -Destination $bundle
if (Test-Path infra/keycloak) { Copy-Item infra/keycloak -Destination $bundle -Recurse }
if (Test-Path .secrets) { Copy-Item .secrets -Destination $bundle -Recurse }
$hashes = Get-ChildItem $bundle -File -Recurse | ForEach-Object {
  [pscustomobject]@{ Path = [IO.Path]::GetRelativePath($bundle,$_.FullName); Hash = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash; Size = $_.Length }
}
[pscustomobject]@{ CreatedAt=(Get-Date).ToUniversalTime().ToString('o'); Files=@($hashes) } | ConvertTo-Json -Depth 4 | Set-Content -Encoding utf8 (Join-Path $bundle "manifest.sha256.json")
Write-Host "Backup criado em $bundle. Armazene-o cifrado fora do host."
