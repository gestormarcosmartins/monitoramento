<#
.SYNOPSIS
  Applies a staged Business Worker update while the service is stopped, with probation and rollback (Windows).
.DESCRIPTION
  Runs as LocalSystem from the service's failure action (the worker exits with code 75 after staging a package, and also on
  any crash). The package is never trusted because of where it sits: the currently installed binary re-verifies the central's
  signed manifest, the size and the SHA-256 (--verify-staged) before anything is unpacked. If the new version does not report
  healthy within -MaxBoots starts, the previous version is restored. Never throws: the service is always started again.
.PARAMETER NoService
  Tests only: do not touch the Windows service.
#>
param(
  [string]$InstallDirectory = "$env:ProgramFiles\Nexora\BusinessWorker",
  [string]$DataDirectory = "$env:ProgramData\Nexora\BusinessWorker",
  [string]$BinaryName = "Nexora.BusinessWorker.exe",
  [string]$ServiceName = "NexoraBusinessWorker",
  [int]$MaxBoots = 3,
  [switch]$NoService
)
$ErrorActionPreference = "Stop"
$updates = Join-Path $DataDirectory "updates"
$state = Join-Path $DataDirectory "apply-state"          # SYSTEM/Administrators only: the service cannot write here
$previousDirectory = "$InstallDirectory.previous"
$probation = Join-Path $state "probation.json"

function Write-Log($message) { Write-Host "nexora-apply-update: $message" }

function Write-Outcome($version, $result, $error) {
  New-Item -ItemType Directory -Force -Path $updates | Out-Null
  [pscustomobject]@{ version = "$version"; result = $result; error = "$error" } | ConvertTo-Json -Compress |
    Set-Content -LiteralPath (Join-Path $updates "last-applied.json") -Encoding ascii
}

function Get-InstalledVersion($directory) {
  try { $out = & (Join-Path $directory $BinaryName) --version 2>$null; return (("$out" -replace "[^0-9.]", "")) } catch { return "" }
}

function Get-StagedVersion {
  try { return ((Get-Content -Raw -LiteralPath (Join-Path $updates "ready\pending.json") | ConvertFrom-Json).version) } catch { return "" }
}

function Reject-Staged($code) {
  Write-Log "staged update rejected: $code"
  $version = Get-StagedVersion
  $rejected = Join-Path $updates "rejected"
  New-Item -ItemType Directory -Force -Path $rejected | Out-Null
  $target = Join-Path $rejected (Get-Date -Format "yyyyMMdd-HHmmss")
  try { Move-Item -LiteralPath (Join-Path $updates "ready") -Destination $target -Force } catch { Remove-Item -Recurse -Force -LiteralPath (Join-Path $updates "ready") -ErrorAction SilentlyContinue }
  Write-Outcome $version "FAILED" $code
}

function Invoke-ProbationCheck {
  if (-not (Test-Path -LiteralPath $probation)) { return }
  $p = Get-Content -Raw -LiteralPath $probation | ConvertFrom-Json
  $confirmedPath = Join-Path $updates "confirmed"
  $confirmed = if (Test-Path -LiteralPath $confirmedPath) { ((Get-Content -Raw -LiteralPath $confirmedPath) -replace "[^0-9.]", "") } else { "" }
  if ($confirmed -eq $p.version) {
    Write-Log "version $($p.version) confirmed healthy"
    Remove-Item -Force -LiteralPath $probation
    return
  }
  $boots = [int]$p.boots + 1
  if ($boots -le $MaxBoots) {
    $p.boots = $boots
    $p | ConvertTo-Json -Compress | Set-Content -LiteralPath $probation -Encoding ascii
    Write-Log "version $($p.version) not confirmed yet (start $boots of $MaxBoots)"
    return
  }
  if (Test-Path -LiteralPath $previousDirectory) {
    Write-Log "version $($p.version) never became healthy; rolling back to $($p.previous)"
    $failed = "$InstallDirectory.failed"
    if (Test-Path -LiteralPath $failed) { Remove-Item -Recurse -Force -LiteralPath $failed }
    Move-Item -LiteralPath $InstallDirectory -Destination $failed
    Move-Item -LiteralPath $previousDirectory -Destination $InstallDirectory
    Remove-Item -Recurse -Force -LiteralPath $failed -ErrorAction SilentlyContinue
    Write-Outcome $p.version "ROLLED_BACK" "never_healthy"
  }
  Remove-Item -Force -LiteralPath $probation
}

function Test-UnsafeZip($zipPath) {
  Add-Type -AssemblyName System.IO.Compression.FileSystem
  try { $zip = [System.IO.Compression.ZipFile]::OpenRead($zipPath) } catch { return $true }
  try {
    $names = @($zip.Entries | ForEach-Object { $_.FullName })
    if ($names.Count -eq 0) { return $true }
    foreach ($name in $names) {
      if ($name.StartsWith("/") -or $name.StartsWith("\") -or $name -match "^[A-Za-z]:" -or $name -match "(^|[\\/])\.\.([\\/]|$)") { return $true }
    }
    foreach ($entry in $zip.Entries) { if (($entry.ExternalAttributes -shr 16) -band 0xA000 -eq 0xA000) { return $true } }  # unix symlink bit
    return -not ($names -contains $BinaryName)
  } finally { $zip.Dispose() }
}

function Invoke-ApplyStaged {
  $ready = Join-Path $updates "ready"
  if (-not (Test-Path -LiteralPath (Join-Path $ready "pending.json"))) { return }
  $binary = Join-Path $InstallDirectory $BinaryName
  Push-Location $InstallDirectory   # the binary reads appsettings.json from its working directory
  try { $verdict = ("$(& $binary --verify-staged $updates 2>$null | Select-Object -Last 1)").Trim() } finally { Pop-Location }
  $version = Get-StagedVersion
  if ($verdict -ne "ok") { Reject-Staged ("verify_" + $(if ($verdict) { $verdict } else { "failed" })); return }
  $package = Join-Path $ready "package.pkg"
  if (Test-UnsafeZip $package) { Reject-Staged "unsafe_archive"; return }

  $fresh = Join-Path $state "new"
  if (Test-Path -LiteralPath $fresh) { Remove-Item -Recurse -Force -LiteralPath $fresh }
  New-Item -ItemType Directory -Force -Path $fresh | Out-Null
  try { Add-Type -AssemblyName System.IO.Compression.FileSystem; [System.IO.Compression.ZipFile]::ExtractToDirectory($package, $fresh) }
  catch { Remove-Item -Recurse -Force -LiteralPath $fresh -ErrorAction SilentlyContinue; Reject-Staged "unpack_failed"; return }
  if (-not (Test-Path -LiteralPath (Join-Path $fresh $BinaryName))) { Remove-Item -Recurse -Force -LiteralPath $fresh; Reject-Staged "binary_missing"; return }
  $settingsFile = Join-Path $InstallDirectory "appsettings.json"
  if (Test-Path -LiteralPath $settingsFile) { Copy-Item -LiteralPath $settingsFile -Destination (Join-Path $fresh "appsettings.json") -Force }  # local settings are not part of a release
  try { Get-Acl -LiteralPath $InstallDirectory | Set-Acl -LiteralPath $fresh } catch { Write-Log "could not copy the folder permissions" }

  $previousVersion = Get-InstalledVersion $InstallDirectory
  if (Test-Path -LiteralPath $previousDirectory) { Remove-Item -Recurse -Force -LiteralPath $previousDirectory }
  try {
    Move-Item -LiteralPath $InstallDirectory -Destination $previousDirectory
    try { Move-Item -LiteralPath $fresh -Destination $InstallDirectory }
    catch { Move-Item -LiteralPath $previousDirectory -Destination $InstallDirectory; throw }
  } catch { Reject-Staged "swap_failed"; return }
  [pscustomobject]@{ version = $version; previous = $previousVersion; boots = 0 } | ConvertTo-Json -Compress | Set-Content -LiteralPath $probation -Encoding ascii
  Remove-Item -Recurse -Force -LiteralPath $ready -ErrorAction SilentlyContinue
  Remove-Item -Force -LiteralPath (Join-Path $updates "confirmed") -ErrorAction SilentlyContinue
  Write-Outcome $version "APPLIED" ""
  Write-Log "applied $version (previous $previousVersion); on probation until it reports healthy"
}

try {
  New-Item -ItemType Directory -Force -Path $state | Out-Null
  if (Test-Path -LiteralPath (Join-Path $InstallDirectory $BinaryName)) {
    Invoke-ProbationCheck
    Invoke-ApplyStaged
  } else { Write-Log "installed binary not found; nothing to do" }
} catch {
  Write-Log "unexpected error: $($_.Exception.GetType().Name)"
} finally {
  if (-not $NoService) { try { Start-Service -Name $ServiceName } catch { Write-Log "could not start the service: $($_.Exception.GetType().Name)" } }
}
exit 0
