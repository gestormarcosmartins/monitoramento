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
$exe = Join-Path $SourceDirectory "Nexora.BusinessWorker.exe"
if (-not (Test-Path -LiteralPath $exe)) { throw "Worker binary not found in $SourceDirectory. Run build-business-worker.ps1 first." }

$secrets = Join-Path $DataDirectory "secrets"
New-Item -ItemType Directory -Force $InstallDirectory, $secrets | Out-Null
Copy-Item -Path (Join-Path $SourceDirectory "*") -Destination $InstallDirectory -Recurse -Force

# Secrets: only SYSTEM, Administrators and the service account may read them.
icacls $DataDirectory /inheritance:r /grant:r "SYSTEM:(OI)(CI)F" "Administrators:(OI)(CI)F" "NT SERVICE\${serviceName}:(OI)(CI)R" | Out-Null
$plain = [System.Net.NetworkCredential]::new("", $Token).Password
Set-Content -LiteralPath (Join-Path $secrets "worker.token") -Value $plain -NoNewline -Encoding ascii
if ($DatabaseRef) {
  if ($DatabaseRef -notmatch '^[a-z][a-z0-9_]{1,63}$') { throw "DatabaseRef must match ^[a-z][a-z0-9_]{1,63}$" }
  if (-not $ConnectionString) { throw "ConnectionString is required with DatabaseRef. Use a READ-ONLY database login." }
  Set-Content -LiteralPath (Join-Path $secrets "$DatabaseRef.conn") -Value $ConnectionString -NoNewline -Encoding utf8
}

$settings = @{
  Nexora = @{
    ServerUrl = $ServerUrl.TrimEnd('/'); TenantId = $TenantId; WorkerId = $WorkerId; SigningPublicKey = $SigningPublicKey
    TokenFile = (Join-Path $secrets "worker.token"); SecretsDirectory = $secrets
    PollSeconds = 30; HeartbeatSeconds = 30; AllowUntrustedServerCertificate = $false; AssumeUtcForUnspecified = $true
  }
  Logging = @{ LogLevel = @{ Default = "Information"; Microsoft = "Warning" } }
}
$settings | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $InstallDirectory "appsettings.json") -Encoding utf8

if (Get-Service $serviceName -ErrorAction SilentlyContinue) { Stop-Service $serviceName -Force; sc.exe delete $serviceName | Out-Null; Start-Sleep 2 }
New-Service -Name $serviceName -DisplayName "Nexora Business Worker" -BinaryPathName "`"$InstallDirectory\Nexora.BusinessWorker.exe`"" `
  -StartupType Automatic -Description "Executes approved read-only business queries for Nexora. Independent from the Nexora Monitoring Agent." | Out-Null
sc.exe config $serviceName obj= "NT SERVICE\$serviceName" | Out-Null          # virtual account, no password, least privilege
sc.exe sidtype $serviceName unrestricted | Out-Null
sc.exe failure $serviceName reset= 86400 actions= restart/5000/restart/5000/restart/30000 | Out-Null   # self-restart; never touches the infra agent
Start-Service $serviceName
Write-Host "Nexora Business Worker installed and started. Infrastructure agent was not touched."
Write-Host "Logs: Windows Event Viewer > Application > source 'Nexora Business Worker'."
