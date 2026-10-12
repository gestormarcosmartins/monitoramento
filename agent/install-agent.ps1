param(
  [string]$ServerUrl = "https://nexora.simmservices.com.br",
  [Parameter(Mandatory=$true)][string]$EnrollmentToken,
  [string]$AgentName = $env:COMPUTERNAME,
  [string]$SnapshotDirectory = "C:\ProgramData\Nexora\Monitor\archive",
  [ValidateRange(10,86400)][int]$SendIntervalSeconds = 30
)
$ErrorActionPreference = "Stop"
$installDirectory = "C:\Program Files\Nexora Agent"
New-Item -ItemType Directory -Path $installDirectory -Force | Out-Null
Copy-Item (Join-Path $PSScriptRoot "dist\win-x64\*") $installDirectory -Recurse -Force
$settings = @{
  Agent = @{
    ServerUrl = $ServerUrl.TrimEnd('/')
    TenantId = ""
    EnrollmentToken = $EnrollmentToken
    AgentName = $AgentName
    SnapshotDirectory = $SnapshotDirectory
    PollSeconds = $SendIntervalSeconds
    HeartbeatSeconds = 60
  }
} | ConvertTo-Json -Depth 4
Set-Content -LiteralPath (Join-Path $installDirectory "agentsettings.json") -Value $settings -Encoding UTF8
$service = Get-Service -Name "NexoraAgent" -ErrorAction SilentlyContinue
if ($service) { Stop-Service NexoraAgent -Force; sc.exe delete NexoraAgent | Out-Null }
sc.exe create NexoraAgent binPath= "`"$installDirectory\Nexora.Agent.exe`"" start= auto DisplayName= "Nexora Monitoring Agent" | Out-Null
sc.exe description NexoraAgent "Coleta diagnósticos locais e os envia com segurança para a Central Nexora." | Out-Null
Start-Service NexoraAgent
Write-Host "Agente instalado e iniciado."
