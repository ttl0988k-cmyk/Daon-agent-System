param (
    [ValidateSet("start", "stop", "restart", "status")]
    [string]$Action = "start"
)

$port = 8765
$healthUrl = "http://127.0.0.1:$port/health"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$rootDir = Split-Path -Parent $scriptDir
$serviceScript = Join-Path $rootDir "daon_runtime\laya_service.py"

function Check-LayaHealthy {
    try {
        $resp = Invoke-RestMethod -Uri $healthUrl -TimeoutSec 1 -ErrorAction SilentlyContinue
        return ($resp -and $resp.status -eq "ok")
    } catch {
        return $false
    }
}

function Stop-LayaService {
    Write-Host "[Laya] Stopping Laya service..." -ForegroundColor Yellow
    $procs = Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like "*laya_service.py*" }
    foreach ($p in $procs) {
        Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
        Write-Host "  Terminated process $($p.ProcessId)" -ForegroundColor Gray
    }
}

switch ($Action) {
    "status" {
        try {
            $resp = Invoke-RestMethod -Uri $healthUrl -TimeoutSec 2
            Write-Host "=== Laya Service Status: ONLINE ===" -ForegroundColor Green
            $resp | Format-List
        } catch {
            Write-Host "=== Laya Service Status: OFFLINE ===" -ForegroundColor Red
        }
    }

    "stop" {
        Stop-LayaService
        Write-Host "[Laya] Service stopped." -ForegroundColor Green
    }

    "restart" {
        Stop-LayaService
        Start-Sleep -Seconds 1
        & $MyInvocation.MyCommand.Path -Action start
    }

    "start" {
        if (Check-LayaHealthy) {
            Write-Host "[Laya] Service is ALREADY running on http://127.0.0.1:$port" -ForegroundColor Green
            return
        }

        Write-Host "[Laya] Starting Laya Decision Service in background..." -ForegroundColor Cyan
        $psi = New-Object System.Diagnostics.ProcessStartInfo
        $psi.FileName = "python.exe"
        $psi.Arguments = "`"$serviceScript`""
        $psi.WorkingDirectory = $rootDir
        $psi.WindowStyle = [System.Diagnostics.ProcessWindowStyle]::Hidden
        $psi.CreateNoWindow = $true
        $psi.UseShellExecute = $true

        [System.Diagnostics.Process]::Start($psi) | Out-Null

        Write-Host "[Laya] Waiting for service to initialize..." -NoNewline
        $started = $false
        for ($i = 0; $i -lt 30; $i++) {
            Start-Sleep -Seconds 1
            Write-Host "." -NoNewline
            if (Check-LayaHealthy) {
                $started = $true
                break
            }
        }
        Write-Host ""

        if ($started) {
            Write-Host "[Laya] Started successfully on http://127.0.0.1:$port" -ForegroundColor Green
            Invoke-RestMethod -Uri $healthUrl | Format-List
        } else {
            Write-Host "[Laya] Warning: Service did not respond within 30 seconds." -ForegroundColor Yellow
        }
    }
}
