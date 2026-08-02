# DAON Agent System - Portable installer (bundled inside the zip)
# Run this from the folder you extracted the zip into.
# It copies THIS folder to %LOCALAPPDATA%\Programs\DAON Agent System
# and creates Desktop + Start Menu shortcuts.
# ASCII/English comments only (cmd ANSI parsing safety).

$ErrorActionPreference = 'Stop'

$src = $PSScriptRoot
if (-not $src) { $src = Split-Path -Parent $MyInvocation.MyCommand.Path }

$exeName = 'DAON Agent System.exe'
$srcExe = Join-Path $src $exeName
if (-not (Test-Path $srcExe)) {
    Write-Host "[ERROR] '$exeName' not found next to install.ps1." -ForegroundColor Red
    Write-Host "        Extract the full zip first, then run install.ps1 from that folder." -ForegroundColor Red
    exit 1
}

$dst = Join-Path $env:LOCALAPPDATA 'Programs\DAON Agent System'
Write-Host "[1/4] Installing to: $dst"
if (-not (Test-Path $dst)) { New-Item -ItemType Directory -Path $dst -Force | Out-Null }

# Copy everything except this script's own folder (in case user runs inside dst).
Write-Host "[2/4] Copying files (robocopy)..."
$rcArgs = @($src, $dst, '/E', '/R:3', '/W:2', '/NP', '/NFL', '/NDL', '/XD', $dst)
& robocopy @rcArgs | Out-Null
if ($LASTEXITCODE -ge 8) {
    Write-Host "[ERROR] robocopy failed with exit code $LASTEXITCODE" -ForegroundColor Red
    exit 1
}

# Disable auto-update so the app never re-downloads an NSIS installer.
$updateYml = Join-Path $dst 'resources\app-update.yml'
if (Test-Path $updateYml) {
    Write-Host "[3/4] Removing auto-update config (app-update.yml)..."
    Remove-Item $updateYml -Force
}
else {
    Write-Host "[3/4] app-update.yml already absent."
}

# Create shortcuts.
Write-Host "[4/4] Creating shortcuts..."
$dstExe = Join-Path $dst $exeName
$wsh = New-Object -ComObject WScript.Shell

$desktop = [Environment]::GetFolderPath('Desktop')
$lnk1 = Join-Path $desktop 'DAON Agent System.lnk'
$s1 = $wsh.CreateShortcut($lnk1)
$s1.TargetPath = $dstExe
$s1.WorkingDirectory = $dst
$s1.Save()

$startMenu = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs'
$lnk2 = Join-Path $startMenu 'DAON Agent System.lnk'
$s2 = $wsh.CreateShortcut($lnk2)
$s2.TargetPath = $dstExe
$s2.WorkingDirectory = $dst
$s2.Save()

# Verify.
$okExe = Test-Path $dstExe
$okYml = -not (Test-Path $updateYml)
$okLnk = Test-Path $lnk1
Write-Host ""
Write-Host "==== RESULT ===="
Write-Host "exe installed      : $okExe"
Write-Host "auto-update removed: $okYml"
Write-Host "desktop shortcut   : $okLnk"
if ($okExe -and $okLnk) {
    Write-Host "DONE. Launch from the Desktop shortcut." -ForegroundColor Green
    exit 0
}
else {
    Write-Host "FAILED. See messages above." -ForegroundColor Red
    exit 1
}
