# ── DAON 개발 소스 → 설치본 동기화 스크립트 ──
# (param은 스크립트 첫 실행문이어야 하므로 주석 직후에 위치)
param(
    [switch]$Open
)
# 콘솔 한글 출력 인코딩 (cp949 콘솔에서도 UTF-8 메시지가 깨지지 않게)
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }
# 용도: 개발 워크스페이스의 UI 파일(index.html, static/)을 설치본
#       (AppData\Local\Programs\DAON Agent System\resources)으로 복사한다.
# 배경: 설치본 server.exe는 exe 옆 resources\static을 직접 서빙한다
#       (server.py RESOURCE_DIR 로직 — webview 폴더가 없으면 RUN_DIR\static 사용).
#       따라서 소스만 고치고 설치본에 복사하지 않으면 앱 화면에는 반영되지 않는다.
# 사용: powershell -NoProfile -ExecutionPolicy Bypass -File scripts\sync_to_installed.ps1
#       옵션 -Open : 동기화 후 앱 재시작까지 수행

$ErrorActionPreference = 'Stop'

$src = Join-Path $PSScriptRoot '..'

# 설치 폴더명 혼선(공백 "DAON Agent System" vs 하이픈 "daon-agent-system") 제거.
# 실제 설치본은 하이픈 폴더이므로 단일 정의 모듈로 자동 탐색한다.
. (Join-Path $PSScriptRoot 'lib\daon_paths.ps1')

$installedRoot = Resolve-DaonInstalledDir
if (-not $installedRoot) {
    $cands = (Get-DaonInstallCandidates) -join ', '
    Write-Host "[중단] 설치본을 찾을 수 없습니다. 확인한 후보: $cands" -ForegroundColor Red
    Write-Host "        설치 후 다시 실행하세요." -ForegroundColor Yellow
    exit 1
}
$dst = Join-Path $installedRoot 'resources'

if (-not (Test-Path $dst)) {
    Write-Host "[중단] resources 폴더가 없습니다: $dst" -ForegroundColor Red
    exit 1
}
Write-Host "[대상] 설치본: $installedRoot" -ForegroundColor Cyan

# ── 1. 백업 (타임스탬프 폴더, 최근 3개만 유지) ──
$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$backup = Join-Path $dst "_sync_backup_$stamp"
New-Item -ItemType Directory -Path $backup -Force | Out-Null
Copy-Item (Join-Path $dst 'index.html') $backup -Force -ErrorAction SilentlyContinue
if (Test-Path (Join-Path $dst 'static')) {
    Copy-Item (Join-Path $dst 'static') $backup -Recurse -Force
}
# 오래된 백업 정리 (3개 초과분 삭제)
Get-ChildItem $dst -Directory -Filter '_sync_backup_*' |
Sort-Object Name -Descending |
Select-Object -Skip 3 |
Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

# ── 2. 캐시 버스팅 자동 버전업 (전 배포 트리 동시 반영) ──
# 소스를 고쳐도 index.html의 ?v=NN이 그대로면 이미 열린 페이지가 캐시된
# 옛 JS를 계속 실행한다. 동기화 시마다 chat.js 버전을 자동 +1 한다.
#
# [수정] 예전에는 소스 index.html 만 +1 한 뒤 설치본에만 복사했다. 그래서
#   win-unpacked / 포터블 은 낮은 버전, 설치본만 높은 버전이 되어
#   "일부만 갱신된" 불일치가 남았다. 이제 bump 결과를 소스 + 전 배포 트리에
#   동일하게 기록해 어느 실행 경로에서도 같은 캐시 버전을 본다.
$indexPath = Join-Path $src 'index.html'
$idxContent = Get-Content $indexPath -Raw -Encoding UTF8
if ($idxContent -match 'chat\.js\?v=(\d+)') {
    $newV = [int]$Matches[1] + 1
    $idxContent = $idxContent -replace 'chat\.js\?v=\d+', ("chat.js?v=" + $newV)
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($indexPath, $idxContent, $utf8NoBom)

    $bumped = @($indexPath)
    foreach ($t in (Resolve-DaonDeployTargets)) {
        $tIdx = Join-Path $t.Resources 'index.html'
        if (Test-Path $tIdx) {
            [System.IO.File]::WriteAllText($tIdx, $idxContent, $utf8NoBom)
            $bumped += $tIdx
        }
    }
    # 재빌드 자기완결 번들이 쓰는 미러도 맞춰 둔다 (fallback index.html 정합)
    $mirrorIdx = Join-Path $src 'dist_new\index.html'
    if (Test-Path $mirrorIdx) {
        [System.IO.File]::WriteAllText($mirrorIdx, $idxContent, $utf8NoBom)
        $bumped += $mirrorIdx
    }
    Write-Host ("[OK] chat.js 캐시 버전 자동 업그레이드: v=" + $newV + " (" + $bumped.Count + "개 위치 동시 반영)")
    foreach ($b in $bumped) { Write-Host ("     - " + $b) }
}

# ── 3. 동기화: index.html + static/ (JS/CSS/이미지 등 UI 자산 전체) ──
# 주의: Copy-Item에 폴더를 그대로 넘기면 대상 폴더 안에 통째로 중첩 복사
# (static\static 생성)되므로, 내용물 와일드카드로 복사한다.
Copy-Item $indexPath $dst -Force
Copy-Item (Join-Path $src 'static\*') (Join-Path $dst 'static') -Recurse -Force
# 과거 버그로 생긴 중첩 폴더가 있으면 정리
if (Test-Path (Join-Path $dst 'static\static')) {
    Remove-Item (Join-Path $dst 'static\static') -Recurse -Force
    Write-Host '[정리] 잘못 생성됐던 static\static 중첩 폴더 삭제'
}

# ── 3-1. 동기화: 백엔드 런타임 (hermes-agent, api, skills) ──
function Sync-FolderWithRobocopy([string]$sDir, [string]$tDir, [string[]]$xDirs, [string[]]$xFiles) {
    if (-not (Test-Path $sDir)) { return }
    $params = @($sDir, $tDir, '/E', '/R:1', '/W:1', '/NFL', '/NDL', '/NJH', '/NJS')
    if ($xDirs -and $xDirs.Count -gt 0) {
        $params += '/XD'
        $params += $xDirs
    }
    if ($xFiles -and $xFiles.Count -gt 0) {
        $params += '/XF'
        $params += $xFiles
    }
    & robocopy @params *>$null
    if ($LASTEXITCODE -ge 8) {
        Write-Host "[경고] 동기화 실패 (코드 $LASTEXITCODE): $sDir" -ForegroundColor Yellow
    }
}

Sync-FolderWithRobocopy (Join-Path $src 'hermes-agent') (Join-Path $dst 'hermes-agent') @('__pycache__', '.pytest_cache', '.git', 'tests', '.venv', 'node_modules') @('*.pyc')
Sync-FolderWithRobocopy (Join-Path $src 'api') (Join-Path $dst 'api') @('__pycache__', '.pytest_cache', '.git', 'tests') @('*.pyc')
Sync-FolderWithRobocopy (Join-Path $src 'skills') (Join-Path $dst 'skills') @('__pycache__', '.pytest_cache', '.git', 'tests') @('*.pyc')

Write-Host "[OK] 전체 동기화 완료 (UI + hermes-agent + api + skills) → $dst" -ForegroundColor Green
Write-Host "     백업: $backup"

# ── 4. 검증: 핵심 파일 크기 비교 ──
$srcChat = Get-Item (Join-Path $src 'static\modules\chat.js')
$dstChat = Get-Item (Join-Path $dst 'static\modules\chat.js')
if ($srcChat.Length -ne $dstChat.Length) {
    Write-Host "[경고] chat.js 크기 불일치 (src=$($srcChat.Length) dst=$($dstChat.Length))" -ForegroundColor Yellow
    exit 1
}
Write-Host "[OK] chat.js 일치 ($($dstChat.Length) bytes)"

# ── 5. (-Open) 앱 재시작 ──
if ($Open) {
    $app = Get-Process -Name 'DAON Agent System' -ErrorAction SilentlyContinue
    if ($app) {
        Write-Host '실행 중인 앱 종료 중...'
        $app | Stop-Process -Force
    }
    $srv = Get-Process -Name 'server' -ErrorAction SilentlyContinue
    if ($srv) {
        Write-Host '실행 중인 server.exe 종료 중...'
        $srv | Stop-Process -Force
    }
    Start-Sleep -Seconds 2
    # Clean any orphaned _MEI* extraction folders from daon_runtime
    $runtimeDirs = @(
        (Join-Path (Split-Path $dst) 'daon_runtime'),
        'C:\daon\DAON-Portable\resources\daon_runtime'
    )
    foreach ($rd in $runtimeDirs) {
        if (Test-Path $rd) {
            Get-ChildItem -Path $rd -Directory -Filter "_MEI*" -ErrorAction SilentlyContinue |
            Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
    Remove-Item env:ELECTRON_RUN_AS_NODE -ErrorAction SilentlyContinue
    $exe = Get-DaonMainExe -Root $installedRoot
    Start-Process $exe -ArgumentList '--remote-debugging-port=9222'
    Write-Host "[OK] 앱 및 서버 재시작됨 (CDP 9222 활성화)" -ForegroundColor Green
}
else {
    Write-Host '앱 재시작(또는 Ctrl+R)하면 반영됩니다. 즉시 재시작하려면: -Open 옵션'
}
