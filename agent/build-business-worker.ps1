param([ValidateSet("win-x64", "linux-x64")][string]$Runtime = "win-x64", [switch]$Package)
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

if ($Package) {
  # Release package for the signed remote update: copy it to the central's releases folder, then register it (Master API).
  [xml]$csproj = Get-Content -LiteralPath $project
  $version = ($csproj.Project.PropertyGroup | Where-Object { $_.Version } | Select-Object -First 1).Version
  $releases = Join-Path $PSScriptRoot "disteleases"
  New-Item -ItemType Directory -Force $releases | Out-Null
  $extension = if ($Runtime -eq "win-x64") { "zip" } else { "tar.gz" }
  $file = Join-Path $releases "nexora-business-worker-$version-$Runtime.$extension"
  if (Test-Path -LiteralPath $file) { Remove-Item -LiteralPath $file }
  $items = Get-ChildItem -LiteralPath $output | Where-Object { $_.Name -ne "appsettings.json" } | ForEach-Object { $_.Name }  # local settings are never part of a release
  if ($extension -eq "zip") { Compress-Archive -Path ($items | ForEach-Object { Join-Path $output $_ }) -DestinationPath $file }
  else { tar -czf $file -C $output @items; if ($LASTEXITCODE -ne 0) { throw "tar failed." } }
  $hash = (Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash.ToLowerInvariant()
  Write-Host "Release package: $file"
  Write-Host "Version $version, platform $Runtime, SHA-256 $hash"
  Write-Host "Next: copy it to <nexora>/releases/ on the server, then POST /api/v1/master/business/releases with version, platform and file_name."
}
