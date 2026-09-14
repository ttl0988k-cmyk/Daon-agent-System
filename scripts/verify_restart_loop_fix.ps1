# 재기동 루프 근본 수정 검증 스크립트 (2026-09-14)
#
# 검증 대상:
#   ① server.py 의 포트 점유 유예(DAON_PORT_WAIT_SEC): 점유자가 사라지면
#      즉사하지 않고 이어서 기동한다.
#   ② daon-server.spec 의 인코딩 방어: 슬림 빌드가 UnicodeEncodeError 로
#      죽지 않는다 (이미 빌드 산출물 크기로 확인).
#
# 시나리오:
#   A 를 9090 에 띄운다 → B 를 같은 포트로 띄운다(경합) → A 를 죽인다
#   → B 가 "released by previous owner" 로 기동하고 /health 200 이면 PASS.

$ErrorActionPreference = 'Continue'
Set-Location 'c:\daon\Daon agent System'

$exe = (Resolve-Path 'dist\server.exe' -ErrorAction SilentlyContinue)
if (-not $exe) { Write-Host "FAIL: dist\server.exe not found"; exit 1 }
$exe = $exe.Path

$logA = Join-Path $env:TEMP 'daon_verify_A.log'
$logB = Join-Path $env:TEMP 'daon_verify_B.log'
Remove-Item $logA, $logB -ErrorAction SilentlyContinue

function Get-9090Owner {
    $c = Get-NetTCPConnection -LocalPort 9090 -State Listen -ErrorAction SilentlyContinue
    if ($c) { return $c.OwningProcess } else { return $null }
}

function Test-Health {
    try {
        $r = Invoke-WebRequest -UseBasicParsing 'http://127.0.0.1:9090/health' -TimeoutSec 4
        return $r.StatusCode
    }
    catch { return 'ERR' }
}

Write-Host '=== cleanup ==='
Get-Process server -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 3

Write-Host '=== launch A on 9090 ==='
Start-Process $exe -ArgumentList '--no-browser', '--port', '9090' -PassThru `
    -RedirectStandardOutput $logA -RedirectStandardError "$logA.err" | Out-Null

$deadline = (Get-Date).AddSeconds(60)
while ((Get-Date) -lt $deadline) {
    if ((Get-9090Owner)) { break }
    Start-Sleep -Seconds 2
}
$aOwner = Get-9090Owner
Write-Host "A listener PID: $aOwner"
Write-Host "A health: $(Test-Health)"

Write-Host '=== launch B on SAME port 9090 (contention) ==='
Start-Process $exe -ArgumentList '--no-browser', '--port', '9090' -PassThru `
    -RedirectStandardOutput $logB -RedirectStandardError "$logB.err" | Out-Null
# B 는 onefile 압축 해제(3~8초) 후에야 포트 검사에 도달하므로 충분히 기다린다.
Start-Sleep -Seconds 25

$aLog = Get-Content $logA -ErrorAction SilentlyContinue
$bLog = Get-Content $logB -ErrorAction SilentlyContinue
Write-Host '--- A log ---'; $aLog | Select-Object -Last 12
Write-Host '--- B log (expect "waiting for release") ---'; $bLog | Select-Object -Last 12

$bWaiting = ($bLog -join "`n") -match 'waiting up to'
Write-Host "CHECK B waits for release : $bWaiting"

Write-Host '=== kill A (simulates Supervisor killPortOwner) ==='
if ($aOwner) { taskkill /PID $aOwner /T /F | Out-Null }
Start-Sleep -Seconds 18

$bLog2 = Get-Content $logB -ErrorAction SilentlyContinue
Write-Host '--- B log after A killed (expect "released by previous owner") ---'
$bLog2 | Select-Object -Last 20

$bReleased = ($bLog2 -join "`n") -match 'released by previous owner'
$bHealth = Test-Health
Write-Host "CHECK B released+started   : $bReleased"
Write-Host "CHECK B health             : $bHealth"

Write-Host '=== verdict ==='
if ($bWaiting -and $bReleased -and $bHealth -eq 200) {
    Write-Host 'RESULT: PASS - restart loop fix verified (no immediate exit on transient port contention)'
}
else {
    Write-Host ("RESULT: CHECK - bWaiting=$bWaiting bReleased=$bReleased health=$bHealth")
}

Write-Host '=== cleanup ==='
Get-Process server -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
