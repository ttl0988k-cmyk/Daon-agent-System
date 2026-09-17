# [2026-09-17] onefile 부모/자식 PID 추적 수정(server.py) 반영 server.exe 배포 스크립트.
# 새로 빌드한 dist\server.exe 를 설치본 / win-unpacked / release_friend / DAON-Portable 의
# resources\server.exe 로 스왑한다. 실행 중(잠김) 대상은 보고만 하고 건너뛴다(EBUSY).
$ErrorActionPreference = 'Continue'

$src = 'c:\daon\Daon agent System\dist\server.exe'
$targets = @(
    'C:\Users\ttl09\AppData\Local\Programs\daon-agent-system\resources\server.exe',
    'c:\daon\Daon agent System\dist\win-unpacked\resources\server.exe',
    'c:\daon\Daon agent System\release_friend\win-unpacked\resources\server.exe',
    'C:\daon\DAON-Portable\resources\server.exe'
)

$srcInfo = Get-Item $src
$srcLen = $srcInfo.Length
"SRC: $src  {0:N1}MB  {1}" -f ($srcLen / 1MB), $srcInfo.LastWriteTime.ToString('s')

foreach ($t in $targets) {
    if (-not (Test-Path $t)) { "SKIP-NO-TARGET: $t"; continue }
    $curLen = (Get-Item $t).Length
    if ($curLen -eq $srcLen) { "ALREADY-CURRENT: $t"; continue }

    $bak = "$t.bak-20260917"
    try {
        Copy-Item $t $bak -Force -ErrorAction Stop
    }
    catch {
        "BAK-FAIL: $t :: $($_.Exception.Message)"
    }

    try {
        Copy-Item $src $t -Force -ErrorAction Stop
        $newLen = (Get-Item $t).Length
        if ($newLen -eq $srcLen) {
            "OK: $t  ({0:N1}MB)" -f ($newLen / 1MB)
        }
        else {
            "MISMATCH: $t  ($newLen != $srcLen)"
        }
    }
    catch {
        "LOCKED: $t :: $($_.Exception.Message)"
    }
}
