<#
  Testes do apply-agent-update.ps1 em pastas temporarias com um agente de mentira (.cmd; sem tarefa agendada, sem administrador).
    powershell -NoProfile -File agent\tests\Test-ApplyAgentUpdate.ps1
#>
$ErrorActionPreference = "Stop"
$script = Join-Path $PSScriptRoot "..\apply-agent-update.ps1"
$root = Join-Path ([IO.Path]::GetTempPath()) ("nx-agent-apply-" + [Guid]::NewGuid().ToString("N").Substring(0, 8))
New-Item -ItemType Directory -Path $root | Out-Null
Add-Type -AssemblyName System.IO.Compression.FileSystem
$script:fails = 0
function Check($got, $want, $label) { if ("$got" -eq "$want") { Write-Host "PASS $label" } else { Write-Host "FAIL $label (got '$got', want '$want')"; $script:fails++ } }

function New-FakeBinary($dir, $version) {
  New-Item -ItemType Directory -Force -Path $dir | Out-Null
  # --version imprime a versao; --verify-staged imprime o veredito de verdict.txt (na pasta acima da instalacao; padrao ok)
  $cmd = "@echo off`r`nif ""%1""==""--version"" (echo $version) else if ""%1""==""--verify-staged"" (if exist ""%~dp0..\verdict.txt"" (type ""%~dp0..\verdict.txt"") else (echo ok))`r`n"
  Set-Content -LiteralPath (Join-Path $dir "Nexora.Agent.cmd") -Value $cmd -Encoding ascii
}
function New-Package($path, $version, $kind = "") {
  if (Test-Path $path) { Remove-Item $path }
  $zip = [IO.Compression.ZipFile]::Open($path, "Create")
  try {
    if ($kind -ne "no-binary") {
      $e = $zip.CreateEntry("Nexora.Agent.cmd"); $w = New-Object IO.StreamWriter($e.Open())
      $w.Write("@echo off`r`nif ""%1""==""--version"" (echo $version)`r`n"); $w.Dispose()
    }
    if ($kind -eq "with-settings") { $e = $zip.CreateEntry("agentsettings.json"); $w = New-Object IO.StreamWriter($e.Open()); $w.Write('{"Agent":{"ServerUrl":"https://EVIL"}}'); $w.Dispose() }
    if ($kind -eq "traversal") { $e = $zip.CreateEntry("../escape.txt"); $w = New-Object IO.StreamWriter($e.Open()); $w.Write("x"); $w.Dispose() }
    if ($kind -eq "absolute") { $e = $zip.CreateEntry("/abs.txt"); $w = New-Object IO.StreamWriter($e.Open()); $w.Write("x"); $w.Dispose() }
    if ($kind -eq "backslash-traversal") { $e = $zip.CreateEntry("sub/../../escape.txt"); $w = New-Object IO.StreamWriter($e.Open()); $w.Write("x"); $w.Dispose() }
  } finally { $zip.Dispose() }
}
function New-Env {
  $t = Join-Path $root ([Guid]::NewGuid().ToString("N").Substring(0, 6))
  $env = @{ Root = $t; Install = Join-Path $t "install"; Data = Join-Path $t "data" }
  New-FakeBinary $env.Install "2.2.0"
  Set-Content -LiteralPath (Join-Path $env.Install "agentsettings.json") -Value '{"Agent":{"ServerUrl":"https://nexora"}}' -Encoding ascii
  Set-Content -LiteralPath (Join-Path $env.Install "apply-update.ps1") -Value "# script instalado" -Encoding ascii
  New-Item -ItemType Directory -Force -Path (Join-Path $env.Data "updates") | Out-Null
  return $env
}
function Stage($env, $package, $version) {
  $ready = Join-Path $env.Data "updates\ready"; New-Item -ItemType Directory -Force -Path $ready | Out-Null
  Copy-Item $package (Join-Path $ready "package.pkg")
  Set-Content -LiteralPath (Join-Path $ready "pending.json") -Value ('{"version":"' + $version + '","platform":"win-x64"}') -Encoding ascii
}
function Invoke-Apply($env, $probation = 3) {
  $p = Start-Process powershell -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$script`"", "-InstallDirectory", "`"$($env.Install)`"",
    "-DataDirectory", "`"$($env.Data)`"", "-BinaryName", "Nexora.Agent.cmd", "-NoTask", "-ProbationSeconds", "$probation", "-PollSeconds", "1") -Wait -PassThru -WindowStyle Hidden
  return $p.ExitCode
}
function Get-Ver($env) { ("$(& (Join-Path $env.Install 'Nexora.Agent.cmd') --version)").Trim() }
function Get-Outcome($env) { $f = Join-Path $env.Data "updates\last-applied.json"; if (Test-Path $f) { Get-Content -Raw $f | ConvertFrom-Json } else { $null } }
function Confirm-Later($env, $version, $seconds) {
  Start-Job -ScriptBlock { param($path, $v, $s) Start-Sleep -Seconds $s; Set-Content -LiteralPath $path -Value $v -Encoding ascii } -ArgumentList (Join-Path $env.Data "updates\confirmed"), $version, $seconds | Out-Null
}

try {
  $pkg = Join-Path $root "p.zip"

  # 1. pacote verificado + versao nova confirma saude -> fica; configuracao e script preservados; anterior guardada
  $e = New-Env; New-Package $pkg "2.2.1" "with-settings"; Stage $e $pkg "2.2.1"; Confirm-Later $e "2.2.1" 1
  Check (Invoke-Apply $e 20) 0 "1 aplica e sai com 0"
  Check (Get-Ver $e) "2.2.1" "1 versao nova instalada"
  Check ((Get-Content -Raw (Join-Path $e.Install "agentsettings.json")).Trim()) '{"Agent":{"ServerUrl":"https://nexora"}}' "1 agentsettings.json NAO e sobrescrito (nem por um pacote malicioso)"
  Check ((Get-Content -Raw (Join-Path $e.Install "apply-update.ps1")).Trim()) "# script instalado" "1 apply-update.ps1 preservado"
  Check ("$(& (Join-Path "$($e.Install).previous" 'Nexora.Agent.cmd') --version)".Trim()) "2.2.0" "1 versao anterior guardada"
  Check (Get-Outcome $e).result "APPLIED" "1 resultado APPLIED"
  Check (Test-Path (Join-Path $e.Data "updates\ready")) $false "1 pacote preparado consumido"
  Check (Test-Path "$($e.Install).staging") $false "1 area protegida limpa"

  # 2. nunca confirma saude -> volta para a versao anterior
  $e = New-Env; Stage $e $pkg "2.2.1"
  Check (Invoke-Apply $e 3) 0 "2 sai com 0 mesmo sem confirmar"
  Check (Get-Ver $e) "2.2.0" "2 voltou para a versao anterior"
  Check (Get-Outcome $e).result "ROLLED_BACK" "2 resultado ROLLED_BACK"
  Check (Get-Outcome $e).error "never_healthy" "2 motivo never_healthy"

  # 3. confirmacao de OUTRA versao nao vale
  $e = New-Env; Stage $e $pkg "2.2.1"; Confirm-Later $e "9.9.9" 1
  Invoke-Apply $e 4 | Out-Null
  Check (Get-Ver $e) "2.2.0" "3 confirmacao de outra versao nao segura a atualizacao"

  # 4. a reverificacao do binario instalado recusa -> nada muda
  $e = New-Env; Stage $e $pkg "2.2.1"; Set-Content -LiteralPath (Join-Path $e.Root "verdict.txt") -Value "invalid_signature" -Encoding ascii
  Check (Invoke-Apply $e 3) 0 "4 sai com 0"
  Check (Get-Ver $e) "2.2.0" "4 versao inalterada quando a verificacao falha"
  Check (Get-Outcome $e).error "verify_invalid_signature" "4 motivo verify_invalid_signature"
  Check (Test-Path "$($e.Install).previous") $false "4 nada foi trocado (sem pasta previous)"

  # 5. pacotes perigosos ou incompletos
  foreach ($kind in @("traversal", "absolute", "backslash-traversal")) {
    $e = New-Env; New-Package $pkg "2.2.1" $kind; Stage $e $pkg "2.2.1"; Invoke-Apply $e 3 | Out-Null
    Check (Get-Outcome $e).error "unsafe_archive" "5 $kind recusado"
    Check (Get-Ver $e) "2.2.0" "5 $kind nao altera a instalacao"
    Check (Test-Path (Join-Path $e.Root "escape.txt")) $false "5 $kind nao escapou da pasta"
  }
  $e = New-Env; New-Package $pkg "2.2.1" "no-binary"; Stage $e $pkg "2.2.1"; Invoke-Apply $e 3 | Out-Null
  Check (Get-Outcome $e).error "unsafe_archive" "5 pacote sem o executavel recusado"

  # 6. nada preparado -> nao faz nada
  $e = New-Env
  Check (Invoke-Apply $e 3) 0 "6 sem pacote sai com 0"
  Check (Get-Ver $e) "2.2.0" "6 sem pacote nada muda"
  Check ($null -eq (Get-Outcome $e)) $true "6 sem pacote nao grava resultado"
} finally {
  Remove-Item -Recurse -Force -LiteralPath $root -ErrorAction SilentlyContinue
}
if ($script:fails -gt 0) { Write-Host "$script:fails falha(s)"; exit 1 }
Write-Host "todos os testes passaram"
