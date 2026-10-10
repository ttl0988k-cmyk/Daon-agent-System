# deploy_asar.ps1
$ErrorActionPreference = 'Continue'
$root = Split-Path $PSScriptRoot -Parent
$src = Join-Path $root 'app.asar.new'

$targets = @(
    'C:\Users\ttl09\AppData\Local\Programs\daon-agent-system\resources\app.asar',
    'C:\daon\DAON-Portable\resources\app.asar'
)

foreach ($t in $targets) {
    if (Test-Path $t) {
        $stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
        $bak = "$t.bak-$stamp"
        try {
            Copy-Item $t $bak -Force
            Copy-Item $src $t -Force -ErrorAction Stop
            Write-Host "[OK] Swapped: $t" -ForegroundColor Green
        } catch {
            Write-Host "[LOCKED/ERR] $t :: $($_.Exception.Message)" -ForegroundColor Yellow
        }
    }
}
