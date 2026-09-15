# verify_deploy_parity.ps1
# Verify the scroll-anchoring fix is byte-identical across source and the three
# deploy trees (win-unpacked / portable / installed) using SHA256.
# ASCII-only on purpose: PowerShell 5.1 reads BOM-less UTF-8 as ANSI.

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $root

$relFiles = @(
    'static\modules\chat.js',
    'static\modules\approval.js',
    'static\modules\beginner.js',
    'static\modules\modes.js',
    'static\styles.css',
    'index.html'
)

$targets = [ordered]@{
    'win-unpacked' = 'release_friend\win-unpacked\resources'
    'portable'     = 'C:\daon\DAON-Portable\resources'
    'installed'    = (Join-Path $env:LOCALAPPDATA 'Programs\daon-agent-system\resources')
}

function Get-Sha([string]$p) {
    if (-not (Test-Path -LiteralPath $p)) { return $null }
    return (Get-FileHash -LiteralPath $p -Algorithm SHA256).Hash
}

Write-Output '=== frontend files: source vs deploy trees (SHA256) ==='
$fail = 0
foreach ($rel in $relFiles) {
    $srcHash = Get-Sha (Join-Path $root $rel)
    $cells = @()
    foreach ($name in $targets.Keys) {
        $dst = Join-Path $targets[$name] $rel
        $h = Get-Sha $dst
        if ($null -eq $h) {
            $cells += ($name + '=MISSING')
            $fail++
        }
        elseif ($h -eq $srcHash) {
            $cells += ($name + '=MATCH')
        }
        else {
            $cells += ($name + '=DIFF')
            $fail++
        }
    }
    Write-Output ('{0,-24} src={1}  {2}' -f $rel, $srcHash.Substring(0, 12), ($cells -join '  '))
}

Write-Output ''
Write-Output '=== server.exe copies (dist + 3 deploy trees) ==='
$exes = @(
    (Join-Path $root 'dist\server.exe'),
    (Join-Path $root 'release_friend\win-unpacked\resources\server.exe'),
    (Join-Path $targets['portable'] 'server.exe'),
    (Join-Path $targets['installed'] 'server.exe')
)
$exeHashes = @()
foreach ($e in $exes) {
    $h = Get-Sha $e
    if ($null -eq $h) {
        Write-Output ('MISSING  ' + $e)
        $fail++
        continue
    }
    $len = (Get-Item -LiteralPath $e).Length
    $exeHashes += $h
    Write-Output ('{0} | {1} | {2}' -f $len, $h.Substring(0, 16), $e)
}
$uniq = ($exeHashes | Select-Object -Unique).Count
if ($uniq -eq 1) {
    Write-Output '=> all server.exe copies identical (PASS)'
}
else {
    Write-Output ('=> server.exe mismatch, distinct hashes = ' + $uniq + ' (FAIL)')
    $fail++
}

Write-Output ''
Write-Output '=== fix markers present in deployed static/modules/chat.js ==='
$markers = @('_chatPinned', 'forceStickChatBottom', 'chatScrollDownBtn', '_isChatNearBottom')
foreach ($name in $targets.Keys) {
    $f = Join-Path $targets[$name] 'static\modules\chat.js'
    if (-not (Test-Path -LiteralPath $f)) {
        Write-Output ($name + ' : MISSING chat.js')
        $fail++
        continue
    }
    $txt = Get-Content -LiteralPath $f -Raw
    $missing = @()
    foreach ($m in $markers) {
        if ($txt -notmatch [regex]::Escape($m)) { $missing += $m }
    }
    if ($missing.Count -eq 0) {
        Write-Output ($name + ' : markers ' + $markers.Count + '/' + $markers.Count + ' OK')
    }
    else {
        Write-Output ($name + ' : missing -> ' + ($missing -join ', '))
        $fail++
    }
}

Write-Output ''
Write-Output '=== Setup.exe ==='
$setup = Join-Path $root 'release_friend\DAON Agent System Setup 1.0.0.exe'
if (Test-Path -LiteralPath $setup) {
    $i = Get-Item -LiteralPath $setup
    Write-Output ('{0} bytes | {1} | {2}' -f $i.Length, $i.LastWriteTime.ToString('yyyy-MM-ddTHH:mm:ss'), $i.Name)
    $loose = Get-Item -LiteralPath (Join-Path $targets['installed'] 'static\modules\chat.js')
    if ($i.LastWriteTime -gt $loose.LastWriteTime) {
        Write-Output '=> Setup.exe newer than loose resources (fix packaged) PASS'
    }
    else {
        Write-Output '=> WARN: Setup.exe older than loose resources'
        $fail++
    }
}
else {
    Write-Output 'MISSING Setup.exe'
    $fail++
}

Write-Output ''
if ($fail -eq 0) {
    Write-Output '=== RESULT: DEPLOY_PARITY_OK (fail=0) ==='
    exit 0
}
Write-Output ('=== RESULT: DEPLOY_PARITY_FAIL (fail=' + $fail + ') ===')
exit 1
