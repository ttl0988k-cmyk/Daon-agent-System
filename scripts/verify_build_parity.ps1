# verify_build_parity.ps1 — 빌드 산출물 ↔ 포터블 설치 ↔ 바탕화면 바로가기 동일 빌드 검증
# 사용: powershell -NoProfile -ExecutionPolicy Bypass -File scripts/verify_build_parity.ps1
$ErrorActionPreference = 'Continue'

$root = Split-Path $PSScriptRoot -Parent
$unpacked = Join-Path $root 'dist\win-unpacked'
$installDir = Join-Path $env:LOCALAPPDATA 'Programs\DAON Agent System'
$portableZip = Join-Path $root 'release\DAON-Agent-System-1.0.0-portable.zip'
$nsisSetup = Join-Path $root 'dist\DAON Agent System Setup 1.0.0.exe'

function Get-Sha256($p) {
    if (Test-Path -LiteralPath $p) { return (Get-FileHash -LiteralPath $p -Algorithm SHA256).Hash }
    return '<MISSING>'
}

Write-Host '===== 1. Builder ARTIFACTS ====='
foreach ($p in @($unpacked, $nsisSetup, $portableZip)) {
    if (Test-Path -LiteralPath $p) {
        $i = Get-Item -LiteralPath $p
        Write-Host ('  OK   {0}  ({1})' -f $i.FullName, $i.LastWriteTime)
    }
    else {
        Write-Host ('  MISS {0}' -f $p)
    }
}

Write-Host ''
Write-Host '===== 2. app.asar HASH PARITY (unpacked vs installed) ====='
# 주의: 함수명을 H 로 두면 PowerShell 의 h(Get-History) 별칭과 충돌한다.
$hU = Get-Sha256 (Join-Path $unpacked 'resources\app.asar')
$hI = Get-Sha256 (Join-Path $installDir 'resources\app.asar')
Write-Host ('  unpacked : {0}' -f $hU)
Write-Host ('  installed: {0}' -f $hI)
Write-Host ('  MATCH    : {0}' -f ($hU -eq $hI))

Write-Host ''
Write-Host '===== 3. SELF-BUILD BUNDLE in INSTALLED resources ====='
$instRes = Join-Path $installDir 'resources'
foreach ($f in @('_sync_build.py', 'daon-server.spec', 'server.py', 'tts_server.py', 'api\api', 'api\agents', 'app.asar')) {
    $ok = Test-Path -LiteralPath (Join-Path $instRes $f)
    Write-Host ('  {0} {1}' -f $(if ($ok) { 'OK  ' }else { 'MISS' }), $f)
}

Write-Host ''
Write-Host '===== 4. DESKTOP / START MENU SHORTCUT -> INSTALL DIR ====='
$desk = [Environment]::GetFolderPath('Desktop')
$sm = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs'
$ws = New-Object -ComObject WScript.Shell
foreach ($lnk in @((Join-Path $desk 'DAON Agent System.lnk'), (Join-Path $sm 'DAON Agent System.lnk'))) {
    if (Test-Path -LiteralPath $lnk) {
        $s = $ws.CreateShortcut($lnk)
        $targetsInstall = ($s.Arguments -like '*Programs\DAON Agent System*') -or ($s.WorkingDirectory -eq $installDir)
        Write-Host ('  {0}' -f $lnk)
        Write-Host ('     args : {0}' -f $s.Arguments)
        Write-Host ('     wdir : {0}' -f $s.WorkingDirectory)
        Write-Host ('     ->points-to-install={0}' -f $targetsInstall)
    }
    else {
        Write-Host ('  MISS {0}' -f $lnk)
    }
}

Write-Host ''
Write-Host '===== 5. PORTABLE MODE (app-update.yml removed) ====='
$upd = Join-Path $installDir 'resources\app-update.yml'
Write-Host ('  app-update.yml removed = {0}' -f (-not (Test-Path -LiteralPath $upd)))

Write-Host ''
Write-Host '===== 6. RUNTIME DATA PRESERVED (unchanged by /MIR) ====='
$wsDir = Join-Path $installDir '_workspace\dynamic_runs'
Write-Host ('  _workspace\dynamic_runs exists = {0}' -f (Test-Path -LiteralPath $wsDir))
$stateDir = Join-Path $env:LOCALAPPDATA 'DAON Agent System\data'
Write-Host ('  STATE_DIR (%LOCALAPPDATA%\DAON Agent System\data) exists = {0}' -f (Test-Path -LiteralPath $stateDir))
