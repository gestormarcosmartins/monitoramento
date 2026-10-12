param([string]$Runtime = "win-x64")
$ErrorActionPreference = "Stop"
$localSdk = Join-Path (Split-Path $PSScriptRoot -Parent) ".tools\dotnet-sdk\dotnet.exe"
if (Test-Path -LiteralPath $localSdk) {
  $env:PATH = "$(Split-Path $localSdk -Parent);$env:PATH"
}
$env:DOTNET_CLI_HOME = Join-Path (Split-Path $PSScriptRoot -Parent) ".tools\dotnet-home"
$env:DOTNET_SKIP_FIRST_TIME_EXPERIENCE = "1"
$env:DOTNET_CLI_TELEMETRY_OPTOUT = "1"
$env:NUGET_PACKAGES = Join-Path (Split-Path $PSScriptRoot -Parent) ".tools\nuget-packages"
New-Item -ItemType Directory -Path $env:DOTNET_CLI_HOME -Force | Out-Null
New-Item -ItemType Directory -Path $env:NUGET_PACKAGES -Force | Out-Null
$nugetConfig = Join-Path $PSScriptRoot "NuGet.Config"
$project = Join-Path $PSScriptRoot "Nexora.Agent\Nexora.Agent.csproj"
$output = Join-Path $PSScriptRoot "dist\$Runtime"
$sdk = & dotnet --list-sdks 2>$null
if ($LASTEXITCODE -eq 0 -and $sdk) {
  dotnet restore $project -r $Runtime --configfile $nugetConfig
  if ($LASTEXITCODE -ne 0) { throw "Falha ao restaurar dependências do agente." }
  dotnet publish $project -c Release -r $Runtime --self-contained true -p:PublishSingleFile=true -o $output --configfile $nugetConfig --no-restore
  if ($LASTEXITCODE -ne 0) { throw "Falha ao compilar o agente." }
} else {
  docker run --rm -v "${PSScriptRoot}:/src/agent" -w /src/agent mcr.microsoft.com/dotnet/sdk:8.0 `
    dotnet publish Nexora.Agent/Nexora.Agent.csproj -c Release -r $Runtime --self-contained true -p:PublishSingleFile=true -o "/src/agent/dist/$Runtime"
}
if ($Runtime -eq "win-x64") {
  $configProject = Join-Path $PSScriptRoot "Nexora.Agent.Config\Nexora.Agent.Config.csproj"
  $configOutput = Join-Path $PSScriptRoot "dist\config"
  if ($LASTEXITCODE -eq 0 -and $sdk) {
    dotnet restore $configProject -r win-x64 --configfile $nugetConfig
    if ($LASTEXITCODE -ne 0) { throw "Falha ao restaurar dependências do configurador." }
    dotnet publish $configProject -c Release -r win-x64 --self-contained true -p:PublishSingleFile=true -o $configOutput --configfile $nugetConfig --no-restore
    if ($LASTEXITCODE -ne 0) { throw "Falha ao compilar o configurador." }
  } else {
    docker run --rm -v "${PSScriptRoot}:/src/agent" -w /src/agent mcr.microsoft.com/dotnet/sdk:8.0 `
      dotnet publish Nexora.Agent.Config/Nexora.Agent.Config.csproj -c Release -r win-x64 --self-contained true -p:PublishSingleFile=true -o /src/agent/dist/config
  }
  Copy-Item (Join-Path $configOutput "Nexora.Agent.Config.exe") $output -Force
  Copy-Item (Join-Path $PSScriptRoot "install-agent.ps1") $output -Force
  Copy-Item (Join-Path $PSScriptRoot "LEIA-ME-MORADA.txt") $output -Force
  $package = Join-Path $PSScriptRoot "dist\Nexora-Agent-$Runtime.zip"
  if (Test-Path $package) { Remove-Item -LiteralPath $package -Force }
  Compress-Archive -Path (Join-Path $output "*") -DestinationPath $package
  Write-Host "Executável criado em $output\Nexora.Agent.exe"
  Write-Host "Configurador criado em $output\Nexora.Agent.Config.exe"
} elseif ($Runtime -eq "linux-x64") {
  Copy-Item (Join-Path $PSScriptRoot "agentsettings.linux.json") (Join-Path $output "agentsettings.json") -Force
  Copy-Item (Join-Path $PSScriptRoot "install-agent.sh") $output -Force
  Copy-Item (Join-Path $PSScriptRoot "agent.env.example") $output -Force
  $package = Join-Path $PSScriptRoot "dist\Nexora-Agent-$Runtime.tar.gz"
  if (Test-Path $package) { Remove-Item -LiteralPath $package -Force }
  tar -czf $package -C $output .
  if ($LASTEXITCODE -ne 0) { throw "Falha ao criar pacote Linux" }
  Write-Host "Executável criado em $output\Nexora.Agent"
} else {
  throw "Runtime não suportado: $Runtime"
}
Write-Host "Pacote de instalação criado em $package"
