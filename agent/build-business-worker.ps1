param([ValidateSet("win-x64", "linux-x64")][string]$Runtime = "win-x64")
# Publishes the Business Worker as a self-contained single file, independent of the infrastructure agent build.
$ErrorActionPreference = "Stop"
$project = Join-Path $PSScriptRoot "Nexora.BusinessWorker\Nexora.BusinessWorker.csproj"
$output = Join-Path $PSScriptRoot "dist\business-worker\$Runtime"
$nugetConfig = Join-Path $PSScriptRoot "NuGet.Config"
dotnet restore $project -r $Runtime --configfile $nugetConfig
if ($LASTEXITCODE -ne 0) { throw "Restore failed." }
dotnet publish $project -c Release -r $Runtime --self-contained true -p:PublishSingleFile=true -o $output --configfile $nugetConfig --no-restore
if ($LASTEXITCODE -ne 0) { throw "Publish failed." }
dotnet test (Join-Path $PSScriptRoot "Nexora.BusinessWorker.Tests\Nexora.BusinessWorker.Tests.csproj") -c Release
if ($LASTEXITCODE -ne 0) { throw "Worker tests failed." }
Write-Host "Business Worker published to $output"
