# deep_build_probe.ps1 — 각 빌드 사본(빌드트리 / 설치본 / DAON-Portable / 포터블 zip 내부)의
#   핵심 파일 해시·타임스탬프를 대조하여 "같은 빌드" 여부를 실제로 판정한다.
# 사용: powershell -NoProfile -ExecutionPolicy Bypass -File scripts/deep_build_probe.ps1
$ErrorActionPreference = 'Continue'
Add-Type -AssemblyName System.IO.Compression.FileSystem

$root = Split-Path $PSScriptRoot -Parent

# 대조 대상 파일 (사본 루트 기준 상대경로) — 양쪽 레이아웃(루트/resources) 모두 포함
$props = @(
    'resources\app.asar',
    'server.exe',
    'resources\server.exe',
    '_sync_build.py',
    'resources\_sync_build.py',
    'server.py',
    'resources\server.py',
    'resources\daon-server.spec',
    'resources\index.html',
    'resources\config.yaml'
)

$copies = [ordered]@{
    'UNPACKED(dist)'     = Join-Path $root 'dist\win-unpacked'
    'INSTALLED(appdata)' = Join-Path $env:LOCALAPPDATA 'Programs\DAON Agent System'
    'PORTABLE(c:\daon)'  = 'C:\daon\DAON-Portable'
}

function Write-CopyProbe($label, $dir) {
    Write-Host ('===== {0} =====' -f $label)
    Write-Host ('  dir = {0}' -f $dir)
    if (-not (Test-Path -LiteralPath $dir)) { Write-Host '  <MISSING DIR>'; Write-Host ''; return }
    foreach ($p in $props) {
        $full = Join-Path $dir $p
        if (Test-Path -LiteralPath $full) {
            $i = Get-Item -LiteralPath $full
            $h = (Get-FileHash -LiteralPath $full -Algorithm SHA256).Hash
            Write-Host ('  {0,-30} {1}  {2,12}  {3}' -f $p, $h.Substring(0, 16), $i.Length, $i.LastWriteTime.ToString('yyyy-MM-dd HH:mm:ss'))
        }
    }
    Write-Host ''
}

foreach ($k in $copies.Keys) { Write-CopyProbe $k $copies[$k] }

# ---- zip 내부 ----
$zip = Join-Path $root 'release\DAON-Agent-System-1.0.0-portable.zip'
Write-Host ('===== PORTABLE ZIP =====')
Write-Host ('  zip = {0}' -f $zip)
if (Test-Path -LiteralPath $zip) {
    $zi = Get-Item -LiteralPath $zip
    Write-Host ('  zip size={0}  mtime={1}' -f $zi.Length, $zi.LastWriteTime.ToString('yyyy-MM-dd HH:mm:ss'))
    $za = [System.IO.Compression.ZipFile]::OpenRead($zip)
    try {
        Write-Host ('  entry count = {0}' -f $za.Entries.Count)
        Write-Host '  -- root layout (top 8) --'
        $za.Entries | Select-Object -First 8 | ForEach-Object { Write-Host ('    {0}' -f $_.FullName) }

        $names = @('app.asar', 'server.exe', '_sync_build.py', 'server.py', 'daon-server.spec', 'index.html', 'config.yaml')
        $sha = [System.Security.Cryptography.SHA256]::Create()
        Write-Host '  -- entry hashes --'
        foreach ($n in $names) {
            $es = @($za.Entries | Where-Object { $_.Name -ieq $n })
            foreach ($e in $es) {
                $st = $e.Open()
                try { $hash = $sha.ComputeHash($st) } finally { $st.Close() }
                $hex = -join ($hash | ForEach-Object { $_.ToString('x2') })
                Write-Host ('    {0,-52} {1}  {2,12}  {3}' -f $e.FullName, $hex.Substring(0, 16), $e.Length, $e.LastWriteTime.ToString('yyyy-MM-dd HH:mm:ss'))
            }
            if ($es.Count -eq 0) { Write-Host ('    (none) {0}' -f $n) }
        }
    }
    finally { $za.Dispose() }
}
else { Write-Host '  <MISSING ZIP>' }
