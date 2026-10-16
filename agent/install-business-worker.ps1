<#
.SYNOPSIS
  Installs the Nexora Business Worker as its OWN Windows service (separate from the infrastructure agent).

.DESCRIPTION
  Run in an elevated PowerShell on the monitored server. The worker needs only outbound HTTPS to the Nexora central.
  It runs as a least-privilege virtual account, keeps its token and database connection strings in a directory only
  that account and administrators can read, restarts itself on failure and never shares memory, queue or threads with
  the Nexora Monitoring Agent. Installing, upgrading or removing it does not touch the infrastructure agent.

.EXAMPLE
  .\install-business-worker.ps1 -ServerUrl https://monitoramento.suaempresa.com -TenantId <guid> -WorkerId <guid> `
      -Token (Read-Host -AsSecureString "Worker token") -SigningPublicKey <key> `
      -DatabaseRef erp_principal -ConnectionString "Server=sql01;Database=ERP;User Id=nexora_ro;Password=...;"
  Add -AllowUntrustedServerCertificate only when the database is on this same machine with a self-signed certificate.

  The signing key is shown by GET /api/v1/agents/business/signing-key (call it once with the worker headers and
  compare it with what the Master sees). Without -SigningPublicKey the installer refuses to continue.
#>
param(
  [Parameter(Mandatory)][string]$ServerUrl,
  [Parameter(Mandatory)][string]$TenantId,
  [Parameter(Mandatory)][string]$WorkerId,
  [Parameter(Mandatory)][securestring]$Token,
  [Parameter(Mandatory)][string]$SigningPublicKey,
  [string]$DatabaseRef,
  [string]$ConnectionString,
  [switch]$AllowUntrustedServerCertificate,
  [string]$SourceDirectory = (Join-Path $PSScriptRoot "dist\business-worker\win-x64"),
  [string]$InstallDirectory = "$env:ProgramFiles\Nexora\BusinessWorker",
  [string]$DataDirectory = "$env:ProgramData\Nexora\BusinessWorker"
)
$ErrorActionPreference = "Stop"
$serviceName = "NexoraBusinessWorker"
$principal = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw "Run this script as Administrator." }
if ($ServerUrl -notmatch '^https://') { throw "ServerUrl must be https." }
if ([string]::IsNullOrWhiteSpace($SigningPublicKey)) { throw "SigningPublicKey is required (pin it at install time)." }
$plain = [System.Net.NetworkCredential]::new("", $Token).Password
if ([string]::IsNullOrWhiteSpace($plain) -or $plain.Length -lt 20) { throw "O token do worker veio vazio ou curto demais. Rode de novo e, no prompt Token do worker, cole o token (nada aparece na tela) e pressione Enter." }
$exe = Join-Path $SourceDirectory "Nexora.BusinessWorker.exe"
if (-not (Test-Path -LiteralPath $exe)) { throw "Worker binary not found in $SourceDirectory. Run build-business-worker.ps1 first." }

$secrets = Join-Path $DataDirectory "secrets"
New-Item -ItemType Directory -Force $InstallDirectory, $secrets | Out-Null
Copy-Item -Path (Join-Path $SourceDirectory "*") -Destination $InstallDirectory -Recurse -Force

# Secrets: only SYSTEM, Administrators and the service account may read them.
icacls $DataDirectory /inheritance:r /grant:r "SYSTEM:(OI)(CI)F" "Administrators:(OI)(CI)F" "NT SERVICE\${serviceName}:(OI)(CI)R" | Out-Null
# Remote update: the service may write ONLY the updates folder; the apply script and its state are SYSTEM/Administrators-only.
$updates = Join-Path $DataDirectory "updates"
New-Item -ItemType Directory -Force $updates | Out-Null
icacls $updates /grant "NT SERVICE\${serviceName}:(OI)(CI)M" | Out-Null
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "apply-business-worker-update.ps1") -Destination (Join-Path $DataDirectory "apply-update.ps1") -Force
$plain = [System.Net.NetworkCredential]::new("", $Token).Password
Set-Content -LiteralPath (Join-Path $secrets "worker.token") -Value $plain -NoNewline -Encoding ascii
if ($DatabaseRef) {
  if ($DatabaseRef -notmatch '^[a-z][a-z0-9_]{1,63}$') { throw "DatabaseRef must match ^[a-z][a-z0-9_]{1,63}$" }
  if (-not $ConnectionString) { throw "ConnectionString is required with DatabaseRef. Use a READ-ONLY database login." }
  Set-Content -LiteralPath (Join-Path $secrets "$DatabaseRef.conn") -Value $ConnectionString -NoNewline -Encoding utf8
}

if ($AllowUntrustedServerCertificate) { Write-Warning "AllowUntrustedServerCertificate ligado: a conexão continua criptografada, mas o certificado do banco não é validado. Use só quando o banco está nesta mesma máquina (certificado autoassinado do SQL Server)." }
$settings = @{
  Nexora = @{
    ServerUrl = $ServerUrl.TrimEnd('/'); TenantId = $TenantId; WorkerId = $WorkerId; SigningPublicKey = $SigningPublicKey
    TokenFile = (Join-Path $secrets "worker.token"); SecretsDirectory = $secrets
    PollSeconds = 30; HeartbeatSeconds = 30; AllowUntrustedServerCertificate = [bool]$AllowUntrustedServerCertificate; AssumeUtcForUnspecified = $true
    UpdateCheckMinutes = 10; UpdateDirectory = $updates
  }
  Logging = @{ LogLevel = @{ Default = "Information"; Microsoft = "Warning" } }
}
$settings | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $InstallDirectory "appsettings.json") -Encoding utf8

if (Get-Service $serviceName -ErrorAction SilentlyContinue) { Stop-Service $serviceName -Force; sc.exe delete $serviceName | Out-Null; Start-Sleep 2 }
New-Service -Name $serviceName -DisplayName "Nexora Business Worker" -BinaryPathName "`"$InstallDirectory\Nexora.BusinessWorker.exe`"" `
  -StartupType Automatic -Description "Executes approved read-only business queries for Nexora. Independent from the Nexora Monitoring Agent." | Out-Null
sc.exe config $serviceName obj= "NT SERVICE\$serviceName" | Out-Null          # virtual account, no password, least privilege
sc.exe sidtype $serviceName unrestricted | Out-Null
# After a crash OR after the worker stages an update and exits (code 75), the failure action runs the apply script as SYSTEM:
# it applies a verified package (with probation and rollback) and always starts the service again. Never touches the infra agent.
$applyCommand = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$DataDirectorypply-update.ps1`" -InstallDirectory `"$InstallDirectory`" -DataDirectory `"$DataDirectory`""
sc.exe failureflag $serviceName 1 | Out-Null
sc.exe failure $serviceName reset= 86400 command= $applyCommand actions= run/2000/run/5000/run/30000 | Out-Null
Start-Service $serviceName
Write-Host "Nexora Business Worker installed and started. Infrastructure agent was not touched."
Write-Host "Logs: Windows Event Viewer > Application > source 'Nexora Business Worker'."
