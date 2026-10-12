param([Parameter(Mandatory=$true)][string]$Bundle,[switch]$ConfirmIsolatedRestore)
$ErrorActionPreference = "Stop"
if (-not $ConfirmIsolatedRestore) { throw "Use -ConfirmIsolatedRestore para criar somente destinos temporários e descartáveis." }
$source = [IO.Path]::GetFullPath($Bundle)
$centralDump = Join-Path $source "central_monitor.dump"
$glpiDump = Join-Path $source "glpi.sql"
$manifestPath = Join-Path $source "manifest.sha256.json"
if (-not (Test-Path -LiteralPath $centralDump)) { throw "Bundle PostgreSQL ausente." }
if (-not (Test-Path -LiteralPath $manifestPath)) { throw "Manifesto SHA-256 ausente." }

$manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
foreach ($file in $manifest.Files) {
  $candidate = [IO.Path]::GetFullPath((Join-Path $source $file.Path))
  if (-not $candidate.StartsWith($source,[StringComparison]::OrdinalIgnoreCase)) { throw "Caminho inválido no manifesto: $($file.Path)" }
  if (-not (Test-Path -LiteralPath $candidate)) { throw "Arquivo ausente no bundle: $($file.Path)" }
  $actual = (Get-FileHash -LiteralPath $candidate -Algorithm SHA256).Hash
  if ($actual -ne $file.Hash) { throw "Hash divergente no bundle: $($file.Path)" }
}

$suffix = ([guid]::NewGuid().ToString('N')).Substring(0,10)
$network = "nexora-restore-$suffix"
$pgContainer = "nexora-restore-pg-$suffix"
$pgVolume = "nexora-restore-pgdata-$suffix"
$glpiContainer = "nexora-restore-glpi-$suffix"
$glpiVolume = "nexora-restore-glpidata-$suffix"
$pgPassword = "restore-$suffix"
$glpiPassword = "restore-$suffix"
$result = [ordered]@{ StartedAt=(Get-Date).ToUniversalTime().ToString('o'); Bundle=$source; PostgreSQL=$null; GLPI=$null }

function Wait-ContainerReady([string]$Name,[scriptblock]$Probe,[int]$Attempts=60) {
  for ($attempt=1;$attempt -le $Attempts;$attempt++) {
    & $Probe
    if ($LASTEXITCODE -eq 0) { return }
    Start-Sleep -Seconds 1
  }
  throw "Container temporário não ficou pronto: $Name"
}

function Wait-MariaDBStable([string]$Name,[int]$Attempts=60) {
  $consecutive = 0
  for ($attempt=1;$attempt -le $Attempts;$attempt++) {
    docker exec $Name sh -c 'mariadb -uroot --password="$MARIADB_ROOT_PASSWORD" -N -e "SELECT 1"' *> $null
    if ($LASTEXITCODE -eq 0) {
      $consecutive++
      if ($consecutive -ge 3) { return }
    } else {
      $consecutive = 0
    }
    Start-Sleep -Seconds 1
  }
  throw "MariaDB temporário não estabilizou: $Name"
}

try {
  docker network create $network | Out-Null
  docker volume create $pgVolume | Out-Null
  docker run -d --name $pgContainer --network $network --mount "source=$pgVolume,target=/var/lib/postgresql/data" -e "POSTGRES_PASSWORD=$pgPassword" -e POSTGRES_DB=central_restore postgres:17-alpine | Out-Null
  Wait-ContainerReady $pgContainer { docker exec $pgContainer pg_isready -U postgres -d central_restore *> $null }
  docker cp $centralDump "${pgContainer}:/tmp/central_monitor.dump"
  docker exec $pgContainer pg_restore -U postgres -d central_restore --no-owner --no-acl /tmp/central_monitor.dump
  if ($LASTEXITCODE -ne 0) { throw "Falha ao restaurar PostgreSQL temporário." }
  $pgEvidence = docker exec $pgContainer psql -U postgres -d central_restore -At -F '|' -c "SELECT (SELECT count(*) FROM cm_schema_migrations),(SELECT count(*) FROM cm_tenants),(SELECT count(*) FROM cm_outbox),(SELECT count(*) FROM cm_consumption_ledger),(SELECT count(*) FROM pg_class WHERE relrowsecurity AND relname LIKE 'cm_%');"
  if ($LASTEXITCODE -ne 0) { throw "Falha ao validar PostgreSQL restaurado." }
  $result.PostgreSQL = $pgEvidence.Trim()

  if (Test-Path -LiteralPath $glpiDump) {
    docker volume create $glpiVolume | Out-Null
    docker run -d --name $glpiContainer --network $network --mount "source=$glpiVolume,target=/var/lib/mysql" -e "MARIADB_ROOT_PASSWORD=$glpiPassword" -e MARIADB_DATABASE=glpi mariadb:11.4 | Out-Null
    Wait-MariaDBStable $glpiContainer
    docker cp $glpiDump "${glpiContainer}:/tmp/glpi.sql"
    docker exec $glpiContainer sh -c 'mariadb -uroot --password="$MARIADB_ROOT_PASSWORD" glpi < /tmp/glpi.sql'
    if ($LASTEXITCODE -ne 0) { throw "Falha ao restaurar GLPI temporário." }
    $glpiEvidence = docker exec $glpiContainer mariadb -uroot "--password=$glpiPassword" -N -e "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='glpi';"
    if ($LASTEXITCODE -ne 0) { throw "Falha ao validar GLPI restaurado." }
    $result.GLPI = $glpiEvidence.Trim()
  }
  $result.FinishedAt=(Get-Date).ToUniversalTime().ToString('o')
  $result.Status='passed'
  $result | ConvertTo-Json -Depth 4
}
finally {
  $knownContainers=@($pgContainer,$glpiContainer)
  foreach ($container in $knownContainers) { if (docker container inspect $container 2>$null) { docker rm -f $container | Out-Null } }
  $knownVolumes=@($pgVolume,$glpiVolume)
  foreach ($volume in $knownVolumes) { if (docker volume inspect $volume 2>$null) { docker volume rm $volume | Out-Null } }
  if (docker network inspect $network 2>$null) { docker network rm $network | Out-Null }
}
