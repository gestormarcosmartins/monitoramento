<#
  Tests for apply-business-worker-update.ps1 on temporary folders with a fake worker binary (a .cmd; no service, no admin).
    powershell -NoProfile -File agent\tests\Test-ApplyUpdate.ps1
#>
$ErrorActionPreference = "Stop"
$script = Join-Path $PSScriptRoot "..\apply-business-worker-update.ps1"
$root = Join-Path ([IO.Path]::GetTempPath()) ("nx-apply-" + [Guid]::NewGuid().ToString("N").Substring(0, 8))
New-Item -ItemType Directory -Path $root | Out-Null
Add-Type -AssemblyName System.IO.Compression.FileSystem
$script:fails = 0
function Check($got, $want, $label) { if ("$got" -eq "$want") { Write-Host "PASS $label" } else { Write-Host "FAIL $label (got '$got', want '$want')"; $script:fails++ } }

function New-FakeBinary($dir, $version) {
  New-Item -ItemType Directory -Force -Path $dir | Out-Null
  # --version prints the version; --verify-staged prints the verdict from verdict.txt next to the data dir (default ok)
  $cmd = "@echo off`r`nif ""%1""==""--version"" (echo $version) else if ""%1""==""--verify-staged"" (if exist ""%~dp0..\verdict.txt"" (type ""%~dp0..\verdict.txt"") else (echo ok))`r`n"
  Set-Content -LiteralPath (Join-Path $dir "Nexora.BusinessWorker.cmd") -Value $cmd -Encoding ascii
}
function New-Package($path, $version, $kind = "") {
  if (Test-Path $path) { Remove-Item $path }
  $zip = [IO.Compression.ZipFile]::Open($path, "Create")
  try {
    if ($kind -ne "no-binary") {
      $e = $zip.CreateEntry("Nexora.BusinessWorker.cmd"); $w = New-Object IO.StreamWriter($e.Open())
      $w.Write("@echo off`r`nif ""%1""==""--version"" (echo $version)`r`n"); $w.Dispose()
    }
    if ($kind -eq "traversal") { $e = $zip.CreateEntry("../escape.txt"); $w = New-Object IO.StreamWriter($e.Open()); $w.Write("x"); $w.Dispose() }
    if ($kind -eq "absolute") { $e = $zip.CreateEntry("/abs.txt"); $w = New-Object IO.StreamWriter($e.Open()); $w.Write("x"); $w.Dispose() }
  } finally { $zip.Dispose() }
}
function New-Env {
  $t = Join-Path $root ([Guid]::NewGuid().ToString("N").Substring(0, 6))
  $env = @{ Install = Join-Path $t "install"; Data = Join-Path $t "data" }
  New-FakeBinary $env.Install "1.0.0"
  Set-Content -LiteralPath (Join-Path $env.Install "appsettings.json") -Value '{"Nexora":{"ServerUrl":"https://x"}}' -Encoding ascii
  New-Item -ItemType Directory -Force -Path (Join-Path $env.Data "updates") | Out-Null
  return $env
}
function Stage($env, $package, $version) {
  $ready = Join-Path $env.Data "updates\ready"; New-Item -ItemType Directory -Force -Path $ready | Out-Null
  Copy-Item $package (Join-Path $ready "package.pkg")
  Set-Content -LiteralPath (Join-Path $ready "pending.json") -Value ('{"version":"' + $version + '","platform":"win-x64"}') -Encoding ascii
}
function Invoke-Apply($env) {
  $p = Start-Process powershell -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$script`"", "-InstallDirectory", "`"$($env.Install)`"",
    "-DataDirectory", "`"$($env.Data)`"", "-BinaryName", "Nexora.BusinessWorker.cmd", "-NoService", "-MaxBoots", "3") -Wait -PassThru -WindowStyle Hidden
  return $p.ExitCode
}
function Get-Ver($env) { ("$(& (Join-Path $env.Install 'Nexora.BusinessWorker.cmd') --version)").Trim() }
function Get-Outcome($env) { $f = Join-Path $env.Data "updates\last-applied.json"; if (Test-Path $f) { (Get-Content -Raw $f | ConvertFrom-Json).result } else { "" } }

try {
  # 1. a verified package is applied; settings survive; previous kept
  $e = New-Env; $pkg = Join-Path $root "p.zip"; New-Package $pkg "1.1.0"; Stage $e $pkg "1.1.0"
  Check (Invoke-Apply $e) 0 "apply exits 0"
  Check (Get-Ver $e) "1.1.0" "new version installed"
  Check ((Get-Content -Raw (Join-Path $e.Install "appsettings.json")).Trim()) '{"Nexora":{"ServerUrl":"https://x"}}' "local settings preserved"
  Check ("$(& (Join-Path "$($e.Install).previous" 'Nexora.BusinessWorker.cmd') --version)".Trim()) "1.0.0" "previous version kept"
  Check (Get-Outcome $e) "APPLIED" "outcome APPLIED"
  Check (Test-Path (Join-Path $e.Data "updates\ready")) $false "staged package consumed"
  # 2. never healthy -> rollback on the 4th unconfirmed start
  1..3 | ForEach-Object { Invoke-Apply $e | Out-Null; Check (Get-Ver $e) "1.1.0" "start $_ of probation keeps the new version" }
  Invoke-Apply $e | Out-Null
  Check (Get-Ver $e) "1.0.0" "rolled back to the previous version"
  Check (Get-Outcome $e) "ROLLED_BACK" "outcome ROLLED_BACK"
  # 3. confirmed by the worker -> no rollback
  $e = New-Env; Stage $e $pkg "1.1.0"; Invoke-Apply $e | Out-Null
  Set-Content -LiteralPath (Join-Path $e.Data "updates\confirmed") -Value "1.1.0" -Encoding ascii
  1..4 | ForEach-Object { Invoke-Apply $e | Out-Null }
  Check (Get-Ver $e) "1.1.0" "confirmed version is never rolled back"
  # 4. the installed binary refuses the package
  $e = New-Env; Stage $e $pkg "1.1.0"; Set-Content -LiteralPath (Join-Path (Split-Path $e.Install) "verdict.txt") -Value "hash_mismatch" -Encoding ascii
  Check (Invoke-Apply $e) 0 "refused update still exits 0"
  Check (Get-Ver $e) "1.0.0" "installed version untouched"
  Check (Get-Outcome $e) "FAILED" "outcome FAILED"
  # 5. unsafe archives
  foreach ($kind in "traversal", "absolute", "no-binary") {
    $e = New-Env; $bad = Join-Path $root "bad.zip"; New-Package $bad "1.1.0" $kind; Stage $e $bad "1.1.0"; Invoke-Apply $e | Out-Null
    Check (Get-Ver $e) "1.0.0" "$kind archive refused"
    Check (Get-Outcome $e) "FAILED" "$kind archive reported as failed"
  }
  Check (Test-Path (Join-Path $root "escape.txt")) $false "no file escaped"
  # 6. nothing staged
  $e = New-Env; Check (Invoke-Apply $e) 0 "nothing staged: exit 0"; Check (Get-Ver $e) "1.0.0" "nothing staged: untouched"
}
finally { Remove-Item -Recurse -Force $root -ErrorAction SilentlyContinue }
if ($script:fails -eq 0) { Write-Host "`nALL PASSED" } else { Write-Host "`n$($script:fails) FAILED" }
exit $script:fails
