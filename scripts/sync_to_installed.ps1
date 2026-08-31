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
$dst = Join-Path $env:LOCALAPPDATA 'Programs\DAON Agent System\resources'

if (-not (Test-Path $dst)) {
    Write-Host "[중단] 설치본을 찾을 수 없습니다: $dst" -ForegroundColor Red
    exit 1
}

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

# ── 2. 캐시 버스팅 자동 버전업 ──
# 소스를 고쳐도 index.html의 ?v=NN이 그대로면 이미 열린 페이지가 캐시된
# 옛 JS를 계속 실행한다. 동기화 시마다 chat.js 버전을 자동 +1 한다.
$indexPath = Join-Path $src 'index.html'
$idxContent = Get-Content $indexPath -Raw -Encoding UTF8
if ($idxContent -match 'chat\.js\?v=(\d+)') {
    $newV = [int]$Matches[1] + 1
    $idxContent = $idxContent -replace 'chat\.js\?v=\d+', ("chat.js?v=" + $newV)
    # UTF-8(BOM 없음)로 저장 — 원본 인코딩 유지
    [System.IO.File]::WriteAllText($indexPath, $idxContent, (New-Object System.Text.UTF8Encoding($false)))
    Write-Host ("[OK] chat.js 캐시 버전 자동 업그레이드: v=" + $newV)
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

Write-Host "[OK] 동기화 완료 → $dst" -ForegroundColor Green
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
        Start-Sleep -Seconds 2
    }
    $exe = Join-Path (Split-Path $dst) 'DAON Agent System.exe'
    Start-Process $exe
    Write-Host "[OK] 앱 재시작됨" -ForegroundColor Green
}
else {
    Write-Host '앱 재시작(또는 Ctrl+R)하면 반영됩니다. 즉시 재시작하려면: -Open 옵션'
}
