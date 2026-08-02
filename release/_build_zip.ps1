# Build the portable zip for DAON Agent System release.
# Stages win-unpacked + bundled scripts, zips them, cleans up.
# Uses .NET ZipFile (more reliable than Compress-Archive for large payloads).

$ErrorActionPreference = 'Stop'
$releaseDir = $PSScriptRoot
if (-not $releaseDir) { $releaseDir = (Get-Location).Path }
$root = Split-Path $releaseDir -Parent

$winUnpacked = Join-Path $root 'dist\win-unpacked'
$stageRoot = Join-Path $releaseDir '_stage'
$stageName = 'DAON-Agent-System-1.0.0-portable'
$stage = Join-Path $stageRoot $stageName
$zipPath = Join-Path $releaseDir 'DAON-Agent-System-1.0.0-portable.zip'

if (-not (Test-Path $winUnpacked)) {
    Write-Host "[ERROR] dist\win-unpacked not found. Build first." -ForegroundColor Red
    exit 1
}

# 0. Remove any stray literal '%STAGE%' folder from earlier failed cmd run.
$stray = Join-Path $root '%STAGE%'
if (Test-Path -LiteralPath $stray) {
    Write-Host "[0] Removing stray literal folder: $stray"
    Remove-Item -LiteralPath $stray -Recurse -Force
}

# 1. Fresh staging folder.
Write-Host "[1] Preparing staging folder: $stage"
if (Test-Path $stageRoot) { Remove-Item $stageRoot -Recurse -Force }
New-Item -ItemType Directory -Path $stage -Force | Out-Null

# 2. Copy win-unpacked into staging (robocopy, fast + robust).
Write-Host "[2] Copying win-unpacked -> staging (robocopy)..."
& robocopy $winUnpacked $stage /E /R:3 /W:2 /NP /NFL /NDL | Out-Null
if ($LASTEXITCODE -ge 8) {
    Write-Host "[ERROR] robocopy failed: exit $LASTEXITCODE" -ForegroundColor Red
    exit 1
}

# 3. Inject bundled installer scripts + README at the zip root.
Write-Host "[3] Injecting bundled install.bat / install.ps1 / README.md..."
Copy-Item (Join-Path $releaseDir 'install.bat') (Join-Path $stage 'install.bat') -Force
Copy-Item (Join-Path $releaseDir 'install.ps1') (Join-Path $stage 'install.ps1') -Force
Copy-Item (Join-Path $releaseDir 'README.md')   (Join-Path $stage 'README.md')   -Force

# 4. Zip the staging folder (top-level entry = DAON-Agent-System-1.0.0-portable).
Write-Host "[4] Creating zip (this may take a few minutes): $zipPath"
if (Test-Path $zipPath) { Remove-Item $zipPath -Force }
Add-Type -AssemblyName System.IO.Compression.FileSystem
[System.IO.Compression.ZipFile]::CreateFromDirectory($stageRoot, $zipPath, [System.IO.Compression.CompressionLevel]::Optimal, $false)

# 5. Cleanup staging.
Write-Host "[5] Cleaning staging..."
Remove-Item $stageRoot -Recurse -Force

# 6. Verify.
$zipInfo = Get-Item $zipPath
$zipMB = [math]::Round($zipInfo.Length / 1MB, 1)
Write-Host ""
Write-Host "==== RESULT ===="
Write-Host ("zip path : {0}" -f $zipPath)
Write-Host ("zip size : {0} MB" -f $zipMB)
Write-Host "DONE." -ForegroundColor Green
