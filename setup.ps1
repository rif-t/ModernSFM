param(
    [Parameter(Mandatory = $true)]
    [string]$SfmGamePath
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path -LiteralPath $PSScriptRoot).Path
$sfmGame = (Resolve-Path -LiteralPath $SfmGamePath).Path
$sfmExe = Join-Path $sfmGame "sfm.exe"
if (-not (Test-Path -LiteralPath $sfmExe -PathType Leaf)) {
    throw "Could not find sfm.exe in SfmGamePath: $sfmGame"
}

$mainMenu = Join-Path $sfmGame "usermod\scripts\sfm\mainmenu"
New-Item -ItemType Directory -Force -Path $mainMenu | Out-Null
Copy-Item -LiteralPath (Join-Path $projectRoot "scripts\modern_sfm_render_job.py") `
    -Destination (Join-Path $mainMenu "modern_sfm_render_job.py") -Force

$configDirectory = Join-Path $env:APPDATA "ModernSFM"
New-Item -ItemType Directory -Force -Path $configDirectory | Out-Null
$config = [ordered]@{
    project_root = $projectRoot
    sfm_game = $sfmGame
}
$configPath = Join-Path $configDirectory "config.json"
$json = $config | ConvertTo-Json
$utf8WithoutBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($configPath, $json, $utf8WithoutBom)

Write-Host "ModernSFM launcher installed in: $mainMenu"
Write-Host "ModernSFM config written to: $configPath"
Write-Host "Restart SFM, load a session, then choose Scripts > modern_sfm_render_job."
