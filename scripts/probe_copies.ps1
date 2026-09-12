# probe_copies.ps1 — 사본별 루트 구조 / 자기수정 백업 / BUILD ID 대조
$ErrorActionPreference = 'Continue'
$root = Split-Path $PSScriptRoot -Parent
$install = Join-Path $env:LOCALAPPDATA 'Programs\DAON Agent System'
$stateData = Join-Path $env:LOCALAPPDATA 'DAON Agent System\data'

function Show-Dir($label, $dir, $filter) {
    Write-Host ('===== {0} : {1} =====' -f $label, $dir)
    if (-not (Test-Path -LiteralPath $dir)) { Write-Host '  <MISSING>'; Write-Host ''; return }
    Get-ChildItem -Force -LiteralPath $dir -ErrorAction SilentlyContinue |
    Where-Object { -not $filter -or $_.Name -match $filter } |
    Sort-Object Name |
    ForEach-Object {
        $kind = if ($_.PSIsContainer) { 'DIR ' } else { 'FILE' }
        Write-Host ('  {0} {1,-42} {2,12}  {3}' -f $kind, $_.Name, $_.Length, $_.LastWriteTime.ToString('yyyy-MM-dd HH:mm:ss'))
    }
    Write-Host ''
}

Show-Dir 'DIST ROOT' (Join-Path $root 'dist\win-unpacked') $null
Show-Dir 'INSTALL ROOT' $install $null
Show-Dir 'INSTALL resources (핵심+bak)' (Join-Path $install 'resources') 'app.asar|server\.exe|index\.html|_sync|daon-server\.spec|\.bak|_sync_backup|daon_runtime'
Show-Dir 'PORTABLE c:\daon ROOT' 'C:\daon\DAON-Portable' $null
Show-Dir 'PORTABLE c:\daon resources' 'C:\daon\DAON-Portable\resources' 'app.asar|server\.exe|index\.html|_sync|daon-server\.spec|\.bak|daon_runtime'
Show-Dir 'STATE data' $stateData '\.log$|ledger|\.json$|\.txt$'

# 자기수정 백업 디렉터리 상세
Write-Host '===== SELF-UPDATE BACKUPS (resources\_sync_backup_*) ====='
Get-ChildItem -Force -LiteralPath (Join-Path $install 'resources') -Directory -ErrorAction SilentlyContinue |
Where-Object { $_.Name -match '_sync_backup' } |
ForEach-Object {
    Write-Host ('  {0}  ({1})' -f $_.FullName, $_.LastWriteTime.ToString('yyyy-MM-dd HH:mm:ss'))
    Get-ChildItem -Force -LiteralPath $_.FullName | ForEach-Object {
        Write-Host ('     - {0,-40} {1,12}  {2}' -f $_.Name, $_.Length, $_.LastWriteTime.ToString('yyyy-MM-dd HH:mm:ss'))
    }
}
Write-Host ''

# BUILD ID 문자열 대조
Write-Host '===== BUILD ID (@ts-check / BUILD ID 문자열) ====='
$htmls = @(
    (Join-Path $root 'dist\win-unpacked\resources\index.html'),
    (Join-Path $install 'resources\index.html'),
    (Join-Path $root 'static\index.html')
)
foreach ($h in $htmls) {
    if (Test-Path -LiteralPath $h) {
        $m = Select-String -LiteralPath $h -Pattern 'BUILD ID|build-id|buildId|v=20[0-9]{2}' -SimpleMatch:$false -ErrorAction SilentlyContinue | Select-Object -First 3
        Write-Host ('  {0}' -f $h)
        if ($m) { $m | ForEach-Object { Write-Host ('     {0}' -f $_.Line.Trim()) } } else { Write-Host '     (no build-id string)' }
    }
}
Write-Host ''

# 자기수정 재빌드 산출물 위치(dist_new?) 및 서버 exe 해시 전수
Write-Host '===== server.exe 전수 (dist 내 모든 후보) ====='
Get-ChildItem -Path (Join-Path $root 'dist') -Recurse -Filter 'server.exe' -ErrorAction SilentlyContinue |
ForEach-Object {
    $h = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.Substring(0, 16)
    Write-Host ('  {0}  {1,12}  {2}  {3}' -f $h, $_.Length, $_.LastWriteTime.ToString('yyyy-MM-dd HH:mm:ss'), $_.FullName)
}
Get-ChildItem -Path (Join-Path $root 'dist_new') -Recurse -Filter 'server.exe' -ErrorAction SilentlyContinue |
ForEach-Object {
    $h = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.Substring(0, 16)
    Write-Host ('  [dist_new] {0}  {1,12}  {2}  {3}' -f $h, $_.Length, $_.LastWriteTime.ToString('yyyy-MM-dd HH:mm:ss'), $_.FullName)
}
Write-Host ''

# 현재 실행 서버 health
Write-Host '===== LIVE SERVER HEALTH ====='
foreach ($u in @('http://127.0.0.1:9090/api/health', 'http://127.0.0.1:9090/health', 'http://127.0.0.1:9090/api/version')) {
    try {
        $r = Invoke-WebRequest -Uri $u -TimeoutSec 3 -UseBasicParsing
        Write-Host ('  {0} -> {1}' -f $u, $r.StatusCode)
        $t = $r.Content
        if ($t.Length -gt 400) { $t = $t.Substring(0, 400) + '...' }
        Write-Host ('     {0}' -f $t)
    }
    catch { Write-Host ('  {0} -> ERR {1}' -f $u, $_.Exception.Message) }
}
