#requires -Version 5.1
# Gera o APK do Nexora (TWA). Primeira execução: o Bubblewrap oferece baixar JDK 17 e Android SDK (~1 GB).
# Requisito: https://simmservices.com.br/m/ no ar com HTTPS e o ícone /m/icon-512.png publicado.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$secretsDir = Join-Path $PSScriptRoot "..\..\.secrets"
New-Item -ItemType Directory -Force $secretsDir | Out-Null
$passFile = Join-Path $secretsDir "nexora-keystore.pass"
$keystore = Join-Path $PSScriptRoot "nexora.keystore"

if (-not (Test-Path $passFile)) {
    $bytes = New-Object byte[] 24
    [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    [Convert]::ToBase64String($bytes) -replace '[+/=]', 'x' | Set-Content -NoNewline $passFile
}
$pass = Get-Content -Raw $passFile

# Bubblewrap lê estas variáveis em vez de perguntar as senhas.
$env:BUBBLEWRAP_KEYSTORE_PASSWORD = $pass
$env:BUBBLEWRAP_KEY_PASSWORD = $pass

if (-not (Test-Path "app")) {
    npx --yes @bubblewrap/cli init --manifest="https://simmservices.com.br/m/manifest.webmanifest" --directory=app
    Copy-Item twa-manifest.json app\twa-manifest.json -Force
}
Set-Location app
Copy-Item ..\twa-manifest.json .\twa-manifest.json -Force
if (-not (Test-Path $keystore)) { Move-Item -ErrorAction SilentlyContinue ..\nexora.keystore . }
npx --yes @bubblewrap/cli build

Write-Host "`nAPK: $PWD\app-release-signed.apk"
Write-Host "Impressão digital SHA-256 para o assetlinks.json:"
keytool -list -v -keystore ..\nexora.keystore -alias nexora -storepass $pass | Select-String "SHA256"
