# == DAON Agent System portable install ==
# Bypass the NSIS installer, which hangs on this machine while extracting the
# 1.1GB server.exe (single-threaded 7z/zip extract blocked by real-time file
# watchers: ProtonDrive / OneDrive / Windows Defender). robocopy streams the
# same payload in seconds with no extract step, so it never hangs.
# Run:  powershell -ExecutionPolicy Bypass -File install-portable.ps1

$ErrorActionPreference = 'Stop'

# 설치 폴더명 혼선 제거 — 단일 정의 모듈 사용 (공백 vs 하이픈).
# 실제 설치 폴더는 하이픈/소문자 "daon-agent-system" 이며, 바로가기도 이 폴더를
# 가리킨다. 과거 공백 폴더로 설치하면 바로가기/자동동기화와 어긋났다.
. (Join-Path $PSScriptRoot 'lib\daon_paths.ps1')

$src = Join-Path $PSScriptRoot 'dist\win-unpacked'

# 이미 설치본이 있으면 그 폴더를 재사용, 없으면 표준(하이픈) 경로로 새로 만든다.
$dst = Resolve-DaonInstalledDir
if (-not $dst) {
    $dst = Join-Path (Join-Path $env:LOCALAPPDATA 'Programs') 'daon-agent-system'
}

if (-not (Test-Path (Join-Path $src 'DAON Agent System.exe'))) {
    Write-Error "source build not found: $src (run from project root)"
    exit 1
}

Write-Host "[1/3] Copying to $dst ..."
# robocopy exit codes 0..7 are success; >= 8 means a real copy failure.
# /MIR (not /E): mirrors the build exactly and DELETES stale files left by
# previous installs (e.g. removed shim modules), so no dead code survives.
# ⚠ /MIR 은 대상에만 있는 파일을 삭제하므로, 런타임 산출물/임시 런타임은 제외한다:
#   - _workspace  : 다이나믹 런(dynamic_runs) 산출물 — 사용자 결과물
#   - data        : (레거시) 설치 경로에 남아 있을 수 있는 상태 디렉터리
#   - resources\daon_runtime : PyInstaller onefile 추출 임시 폴더 (재생성됨)
#   실제 사용자 데이터(STATE_DIR)는 %LOCALAPPDATA%\DAON Agent System\data 로
#   분리되어 있어 애초에 설치 경로 삭제와 무관하다. (api/api/config.py 참조)
& robocopy $src $dst /MIR /XD "$dst\_workspace" "$dst\data" "$dst\resources\daon_runtime" /R:3 /W:2 /NP /NFL /NDL | Out-Null
$rc = $LASTEXITCODE
if ($rc -ge 8) {
    Write-Error "copy failed (robocopy exit $rc)"
    exit 1
}
Write-Host "      copy done (robocopy exit $rc)."

# Disable auto-update so the app does not re-download the NSIS installer
# (which would re-trigger the hang) in portable mode.
$upd = Join-Path $dst 'resources\app-update.yml'
if (Test-Path $upd) {
    Remove-Item -Force $upd
    Write-Host "      removed app-update.yml (portable mode)."
}

Write-Host "[2/3] Creating shortcuts ..."
$exe = Get-DaonMainExe -Root $dst
$launcher = Join-Path $dst 'DAON Agent System.cmd'
# Some developer environments export ELECTRON_RUN_AS_NODE=1 globally.  That
# makes the packaged Electron executable behave like node.exe and exit with
# code 9 before electron/main.js is evaluated.  Always clear it at the
# portable-app boundary so Explorer/Desktop launches are deterministic.
Set-Content -Path $launcher -Encoding ASCII -Value @(
    '@echo off'
    'set "ELECTRON_RUN_AS_NODE="'
    'start "" "%~dp0DAON Agent System.exe" %*'
)
$ws = New-Object -ComObject WScript.Shell
$desk = [Environment]::GetFolderPath('Desktop')
$sm = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs'
New-Item -ItemType Directory -Force -Path $sm | Out-Null
foreach ($lnk in @((Join-Path $desk 'DAON Agent System.lnk'), (Join-Path $sm 'DAON Agent System.lnk'))) {
    $s = $ws.CreateShortcut($lnk)
    $s.TargetPath = Join-Path $env:WINDIR 'System32\cmd.exe'
    $s.Arguments = "/d /c `"`"$launcher`"`""
    $s.WorkingDirectory = $dst
    $s.IconLocation = "$exe,0"
    $s.WindowStyle = 7
    $s.Save()
}
Write-Host "      shortcuts OK."

Write-Host "[3/3] Verify ..."
$ok = (Test-Path $exe) -and (Test-Path $launcher) -and (-not (Test-Path $upd)) `
    -and (Test-Path (Join-Path $desk 'DAON Agent System.lnk'))
Write-Host ("      exe={0} update_yml_removed={1} desktop_lnk={2}" -f `
    (Test-Path $exe), (-not (Test-Path $upd)), (Test-Path (Join-Path $desk 'DAON Agent System.lnk')))

if ($ok) {
    Write-Host ""
    Write-Host "Done. Install dir: $dst"
    Write-Host "Launch via the Desktop / Start Menu shortcut, or:"
    Write-Host "  `"$exe`""
    exit 0
}
else {
    Write-Error "verification failed"
    exit 1
}
