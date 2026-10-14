param(
    [switch]$ResetData
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$EnvFile = Join-Path $ProjectRoot ".env"

if ($ResetData) {
    docker compose --project-directory $ProjectRoot down --volumes --remove-orphans
}

if (-not (Test-Path -LiteralPath $EnvFile)) {
    $template = Get-Content -LiteralPath (Join-Path $ProjectRoot ".env.example") -Raw
    $secrets = @{
        "change-me-admin" = [guid]::NewGuid().ToString("N")
        "change-me-central" = [guid]::NewGuid().ToString("N")
        "change-me-zabbix" = [guid]::NewGuid().ToString("N")
        "change-me-keycloak" = [guid]::NewGuid().ToString("N")
        "change-me-keycloak-admin" = [guid]::NewGuid().ToString("N")
        "change-me-glpi" = [guid]::NewGuid().ToString("N")
        "change-me-rabbitmq" = [guid]::NewGuid().ToString("N")
        "change-me-with-at-least-32-random-characters" = ([guid]::NewGuid().ToString("N") + [guid]::NewGuid().ToString("N"))
    }
    foreach ($entry in $secrets.GetEnumerator()) {
        $template = $template.Replace($entry.Key, $entry.Value)
    }
    Set-Content -LiteralPath $EnvFile -Value $template -Encoding utf8NoBOM
    Write-Host "Arquivo .env criado com segredos aleatorios."
}

docker compose --project-directory $ProjectRoot up --build -d
docker compose --project-directory $ProjectRoot ps

Write-Host ""
$publicUrl = if ($env:CENTRAL_PUBLIC_URL) { $env:CENTRAL_PUBLIC_URL.TrimEnd('/') } else { "http://localhost:8090" }
Write-Host "Central Monitor: $publicUrl"
Write-Host "Keycloak:       $publicUrl/auth/admin"
