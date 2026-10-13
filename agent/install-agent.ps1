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
# A Windows Service (sc.exe) reproducibly broke Microsoft.Data.SqlClient's native SNI
# library in production; a Scheduled Task running as SYSTEM does not. See
# Nexora.Agent.Config/Program.cs's InstallScheduledTask for the same approach.
$service = Get-Service -Name "NexoraAgent" -ErrorAction SilentlyContinue
if ($service) { Stop-Service NexoraAgent -Force; sc.exe delete NexoraAgent | Out-Null }
Unregister-ScheduledTask -TaskName "NexoraAgent" -Confirm:$false -ErrorAction SilentlyContinue
$action = New-ScheduledTaskAction -Execute (Join-Path $installDirectory "Nexora.Agent.exe")
$trigger = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
$settingsSet = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName "NexoraAgent" -Action $action -Trigger $trigger -Principal $principal -Settings $settingsSet -Description "Coleta diagnósticos locais e os envia com segurança para a Central Nexora." -Force | Out-Null
Start-ScheduledTask -TaskName "NexoraAgent"
Write-Host "Agente instalado e iniciado."
