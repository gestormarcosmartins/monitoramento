<#
.SYNOPSIS
  Aplica a atualização preparada do Nexora Agent (Windows), com periodo de teste e volta automatica.
.DESCRIPTION
  Roda como LocalSystem pela tarefa agendada "NexoraAgentUpdate", acionada pelo proprio agente depois de preparar um pacote.
  O pacote preparado NUNCA e confiavel so por estar numa pasta: ele e copiado para uma area so de administradores
  (<Install>.staging), e o binario ATUALMENTE instalado (o confiavel) confere de novo a assinatura da Central, o vinculo a este
  agente, o tamanho e o SHA-256 (--verify-staged) antes de qualquer coisa ser descompactada. A troca copia os arquivos novos por cima
  dos instalados, guarda os anteriores em <Install>.previous e NUNCA toca em agentsettings.json. Depois religa o agente e espera ele
  confirmar saude (um heartbeat aceito pela Central grava o marcador "confirmed"). Se nao confirmar dentro do prazo, restaura os
  arquivos anteriores e religa. Nunca lanca erro para fora: o agente sempre volta a rodar.
.PARAMETER NoTask
  Somente testes: nao mexe na tarefa agendada nem em processos.
#>
param(
  [string]$InstallDirectory = "$env:ProgramFiles\Nexora Agent",
  [string]$DataDirectory = "$env:ProgramData\Nexora\Agent",
  [string]$BinaryName = "Nexora.Agent.exe",
  [string]$TaskName = "NexoraAgent",
  [int]$ProbationSeconds = 420,
  [int]$PollSeconds = 5,
  [switch]$NoTask
)
$ErrorActionPreference = "Stop"
$updates = Join-Path $DataDirectory "updates"
$ready = Join-Path $updates "ready"
$protectedRoot = "$InstallDirectory.staging"            # irmao em Program Files: so administradores escrevem aqui
$protectedReady = Join-Path $protectedRoot "ready"
$previousDirectory = "$InstallDirectory.previous"
$keepFiles = @("agentsettings.json", "apply-update.ps1")  # configuracao e este script nunca sao sobrescritos por um pacote
$processName = [IO.Path]::GetFileNameWithoutExtension($BinaryName)

function Write-Log($message) { Write-Host "nexora-agent-update: $message" }

function Write-Outcome($version, $result, $errorCode) {
  New-Item -ItemType Directory -Force -Path $updates | Out-Null
  [pscustomobject]@{ version = "$version"; result = $result; error = "$errorCode" } | ConvertTo-Json -Compress |
    Set-Content -LiteralPath (Join-Path $updates "last-applied.json") -Encoding ascii
}

function Get-InstalledVersion($directory) {
  try { $out = & (Join-Path $directory $BinaryName) --version 2>$null; return (("$out" -replace "[^0-9.]", "")) } catch { return "" }
}

function Get-StagedVersion {
  try { return ((Get-Content -Raw -LiteralPath (Join-Path $protectedReady "pending.json") | ConvertFrom-Json).version) } catch { return "" }
}

function Reject-Staged($code) {
  Write-Log "pacote recusado: $code"
  $version = Get-StagedVersion
  Remove-Item -Recurse -Force -LiteralPath $protectedRoot -ErrorAction SilentlyContinue
  Remove-Item -Recurse -Force -LiteralPath $ready -ErrorAction SilentlyContinue
  Write-Outcome $version "FAILED" $code
}

function Test-UnsafeZip($zipPath) {
  Add-Type -AssemblyName System.IO.Compression.FileSystem
  try { $zip = [System.IO.Compression.ZipFile]::OpenRead($zipPath) } catch { return $true }
  try {
    $names = @($zip.Entries | ForEach-Object { $_.FullName })
    if ($names.Count -eq 0) { return $true }
    foreach ($name in $names) {
      $flat = $name.Replace('\', '/')
      if ($flat.StartsWith('/') -or $flat -match '^[A-Za-z]:' -or (@($flat -split '/') -contains '..')) { return $true }
    }
    foreach ($entry in $zip.Entries) { if ((($entry.ExternalAttributes -shr 16) -band 0xA000) -eq 0xA000) { return $true } }  # bit de link simbolico
    return -not ($names -contains $BinaryName)
  } finally { $zip.Dispose() }
}

function Stop-Agent {
  if ($NoTask) { return }
  try { Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue } catch { }
  $deadline = (Get-Date).AddSeconds(30)
  while ((Get-Date) -lt $deadline) {
    $running = @(Get-Process -Name $processName -ErrorAction SilentlyContinue)
    if ($running.Count -eq 0) { return }
    $running | Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Milliseconds 500
  }
  Write-Log "o agente nao encerrou em 30 s"
}

function Start-Agent {
  if ($NoTask) { return }
  try { Start-ScheduledTask -TaskName $TaskName } catch { Write-Log "nao foi possivel iniciar a tarefa: $($_.Exception.GetType().Name)" }
}

function Restore-Previous($version, $reason) {
  if (-not (Test-Path -LiteralPath $previousDirectory)) { Write-Log "nao ha versao anterior para restaurar"; return }
  Write-Log "restaurando a versao anterior ($reason)"
  Stop-Agent
  Get-ChildItem -LiteralPath $previousDirectory -Force | Where-Object { $keepFiles -notcontains $_.Name } |
    ForEach-Object { Copy-Item -LiteralPath $_.FullName -Destination $InstallDirectory -Recurse -Force }
  Write-Outcome $version "ROLLED_BACK" $reason
}

function Invoke-Apply {
  if (-not (Test-Path -LiteralPath (Join-Path $ready "pending.json")) -or -not (Test-Path -LiteralPath (Join-Path $ready "package.pkg"))) { Write-Log "nada preparado"; return }
  # 1) copia o pacote para a area protegida ANTES de verificar (nao confia na pasta de trabalho do agente)
  Remove-Item -Recurse -Force -LiteralPath $protectedRoot -ErrorAction SilentlyContinue
  New-Item -ItemType Directory -Force -Path $protectedReady | Out-Null
  Copy-Item -LiteralPath (Join-Path $ready "pending.json") -Destination $protectedReady -Force
  Copy-Item -LiteralPath (Join-Path $ready "package.pkg") -Destination $protectedReady -Force
  # 2) o binario instalado reverifica assinatura, vinculo, tamanho e hash
  $binary = Join-Path $InstallDirectory $BinaryName
  Push-Location $InstallDirectory
  try { $verdict = ("$(& $binary --verify-staged $protectedRoot 2>$null | Select-Object -Last 1)").Trim() } finally { Pop-Location }
  $version = Get-StagedVersion
  if ($verdict -ne "ok") { Reject-Staged ("verify_" + $(if ($verdict) { $verdict } else { "failed" })); return }
  $package = Join-Path $protectedReady "package.pkg"
  if (Test-UnsafeZip $package) { Reject-Staged "unsafe_archive"; return }
  $fresh = Join-Path $protectedRoot "new"
  New-Item -ItemType Directory -Force -Path $fresh | Out-Null
  try { Add-Type -AssemblyName System.IO.Compression.FileSystem; [System.IO.Compression.ZipFile]::ExtractToDirectory($package, $fresh) }
  catch { Reject-Staged "unpack_failed"; return }
  if (-not (Test-Path -LiteralPath (Join-Path $fresh $BinaryName))) { Reject-Staged "binary_missing"; return }

  # 3) guarda a versao atual, troca os arquivos com o agente parado
  $previousVersion = Get-InstalledVersion $InstallDirectory
  Stop-Agent
  if (Test-Path -LiteralPath $previousDirectory) { Remove-Item -Recurse -Force -LiteralPath $previousDirectory }
  New-Item -ItemType Directory -Force -Path $previousDirectory | Out-Null
  Get-ChildItem -LiteralPath $InstallDirectory -Force | ForEach-Object { Copy-Item -LiteralPath $_.FullName -Destination $previousDirectory -Recurse -Force }
  try {
    Get-ChildItem -LiteralPath $fresh -Force | Where-Object { $keepFiles -notcontains $_.Name } |
      ForEach-Object { Copy-Item -LiteralPath $_.FullName -Destination $InstallDirectory -Recurse -Force }
  } catch {
    Write-Log "falha ao copiar os arquivos novos: $($_.Exception.GetType().Name)"
    Restore-Previous $version "swap_failed"
    Start-Agent
    return
  }
  Remove-Item -Recurse -Force -LiteralPath $ready -ErrorAction SilentlyContinue
  Remove-Item -Recurse -Force -LiteralPath $protectedRoot -ErrorAction SilentlyContinue
  Remove-Item -Force -LiteralPath (Join-Path $updates "confirmed") -ErrorAction SilentlyContinue
  Write-Outcome $version "APPLIED" ""
  Write-Log "aplicada a versao $version (anterior $previousVersion); em periodo de teste"
  Start-Agent

  # 4) periodo de teste: a versao nova precisa confirmar saude (um heartbeat aceito) dentro do prazo
  $deadline = (Get-Date).AddSeconds($ProbationSeconds)
  $confirmedPath = Join-Path $updates "confirmed"
  while ((Get-Date) -lt $deadline) {
    if (Test-Path -LiteralPath $confirmedPath) {
      $confirmed = ((Get-Content -Raw -LiteralPath $confirmedPath) -replace "[^0-9.]", "")
      if ($confirmed -eq $version) { Write-Log "versao $version confirmada como saudavel"; return }
    }
    Start-Sleep -Seconds $PollSeconds
  }
  Restore-Previous $version "never_healthy"
  Start-Agent
}

$mutex = $null
try {
  $mutex = New-Object System.Threading.Mutex($false, "Global\NexoraAgentUpdate")
  if (-not $mutex.WaitOne(0)) { Write-Log "outra aplicacao ja esta em andamento"; exit 0 }
  if (Test-Path -LiteralPath (Join-Path $InstallDirectory $BinaryName)) { Invoke-Apply } else { Write-Log "binario instalado nao encontrado; nada a fazer" }
} catch {
  Write-Log "erro inesperado: $($_.Exception.GetType().Name)"
  Start-Agent
} finally {
  if ($mutex) { try { $mutex.ReleaseMutex() } catch { } }
}
exit 0
