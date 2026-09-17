# [2026-09-17] 배포 검증 — 고친 asar/server.exe 마커 확인.
#  · asar 마커(JS 소스가 평문으로 들어있음): adopt / defer / childPid
#  · server.pid 기록 마커는 server.exe(압축 CArchive) 안이라 직접 grep 불가 → canary 로 검증됨.
$ErrorActionPreference = 'Continue'

$src = 'c:\daon\Daon agent System\app.asar.new'

$targets = @(
    'C:\Users\ttl09\AppData\Local\Programs\daon-agent-system\resources',
    'c:\daon\Daon agent System\dist\win-unpacked\resources',
    'c:\daon\Daon agent System\release_friend\win-unpacked\resources',
    'C:\daon\DAON-Portable\resources'
)

# release_friend 는 아직 옛 asar 였으므로 먼저 갱신한다.
$rf = 'c:\daon\Daon agent System\release_friend\win-unpacked\resources\app.asar'
if (Test-Path $rf) {
    $rfLen = (Get-Item $rf).Length
    $srcLen = (Get-Item $src).Length
    if ($rfLen -ne $srcLen) {
        Copy-Item $rf "$rf.bak-20260917" -Force -ErrorAction SilentlyContinue
        try {
            Copy-Item $src $rf -Force -ErrorAction Stop
            "RELEASE_FRIEND_ASAR_UPDATED -> {0:N0}B" -f (Get-Item $rf).Length
        }
        catch {
            "RELEASE_FRIEND_ASAR_FAIL :: $($_.Exception.Message)"
        }
    }
    else {
        "RELEASE_FRIEND_ASAR_ALREADY_CURRENT"
    }
}

"--- 마커 검증 ---"
foreach ($res in $targets) {
    $asar = Join-Path $res 'app.asar'
    $se = Join-Path $res 'server.exe'
    if (-not (Test-Path $asar)) { "MISSING asar: $res"; continue }
    if (-not (Test-Path $se)) { "MISSING server.exe: $res"; continue }

    $txt = [System.IO.File]::ReadAllText($asar, [System.Text.Encoding]::UTF8)
    $m1 = $txt.Contains('adopting instead of respawning')
    $m2 = $txt.Contains('deferring to port-liveness adopt')
    $m3 = $txt.Contains('_loadChildPid')
    $m4 = $txt.Contains('server.pid')

    $asarKb = [math]::Round((Get-Item $asar).Length / 1KB, 1)
    $seMb = [math]::Round((Get-Item $se).Length / 1MB, 1)
    "{0}`n   asar={1}KB  server.exe={2}MB  M1_ADOPT={3}  M2_DEFER={4}  M3_LOADPID={5}  M4_PIDFILE={6}" -f `
    (Split-Path $res -Parent), $asarKb, $seMb, $m1, $m2, $m3, $m4
}
