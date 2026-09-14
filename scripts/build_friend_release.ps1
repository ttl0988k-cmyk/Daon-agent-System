# build_friend_release.ps1 -- Build a friend-distributable Setup.exe
#
# WHY:
#   Bundled files leak the developer's local identity:
#     - static/store/*.json      : plugin catalog "path", Obsidian vault hint
#     - daon-server.spec         : a comment mentioning C:\Users\<user>\...
#   All are bundled into the installer via electron-builder (extraResources /
#   from:"static"). Shipping as-is leaks the developer username to friends.
#
# STRATEGY (relocate -> sanitize -> build -> restore):
#   1) MOVE every store file OUT to an external backup dir (outside the packaged
#      tree) so no *.bak / *.friendbak can ever be bundled.
#   2) write back ONLY the primary catalogs (plugins.json, connectors.json),
#      sanitized (user paths tokenized). Also sanitize daon-server.spec in place.
#   3) build electron-builder -> separate output dir (existing dist untouched).
#   4) finally: ALWAYS remove the sanitized copies and restore every original.
#
# SAFETY:
#   - restore runs in finally -> survives build failure.
#   - backups live in _friend_build_backup_<stamp> (root), NOT under static/,
#     so electron-builder can never package them.
#   - output goes to release_friend -> dist\*.exe and dist\server.exe stay intact.
#   - the operator's own installed copy is NOT touched by this script.
#
# USAGE:
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\build_friend_release.ps1

param(
    [string]$OutDir = 'release_friend'
)

$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

$root = Split-Path $PSScriptRoot -Parent
Set-Location $root

$builder = Join-Path $root 'node_modules\.bin\electron-builder.cmd'
if (-not (Test-Path $builder)) {
    Write-Host "[ABORT] electron-builder not found: $builder" -ForegroundColor Red
    Write-Host "        run 'npm install' first."
    exit 1
}

# Store dirs: source static\store + dist_new\static\store (identical copies).
$storeDirs = @(
    (Join-Path $root 'static\store'),
    (Join-Path $root 'dist_new\static\store')
)
# Loose files to sanitize in place (also bundled into the installer).
$looseFiles = @(
    (Join-Path $root 'daon-server.spec')
)

$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$backupRoot = Join-Path $root ("_friend_build_backup_" + $stamp)   # OUTSIDE packaged paths
New-Item -ItemType Directory -Path $backupRoot -Force | Out-Null

$journal = @()   # { orig, backup }   originals moved/copied out
$written = @()   # sanitized primaries we created (deleted before restore)
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)

# Tokenize developer identity.  Korean chars via \uXXXX so this file stays ASCII:
#   OneDrive\<U+BB38 U+C11C>\<U+BCC4 U+C790 U+B9AC>  ->  OneDrive\Documents\MyVault
function Sanitize-Text([string]$raw) {
    $t = $raw -replace 'ttl09', 'USER'
    $t = $t -replace 'OneDrive\\+[\uBB38\uC11C\\]+[\uBCC4\uC790\uB9AC]+', 'OneDrive\Documents\MyVault'
    return $t
}

Write-Host '-- [1] relocate store files out + sanitize primaries --'
$di = 0
foreach ($dir in $storeDirs) {
    $di++
    if (-not (Test-Path $dir)) { continue }
    $destDir = Join-Path $backupRoot ("d$di")
    New-Item -ItemType Directory -Path $destDir -Force | Out-Null
    Get-ChildItem $dir -File -Force | ForEach-Object {
        $orig = $_.FullName
        $bak = Join-Path $destDir $_.Name
        Move-Item $orig $bak -Force
        $journal += [pscustomobject]@{ orig = $orig; backup = $bak }

        # Re-create ONLY the primary catalogs; skip any *.bak / *.friendbak so
        # they can never be bundled into the installer.
        if ($_.Name -match '\.json$' -and $_.Name -notmatch 'bak') {
            $raw = Get-Content $bak -Raw -Encoding UTF8
            $clean = Sanitize-Text $raw
            [System.IO.File]::WriteAllText($orig, $clean, $utf8NoBom)
            $written += $orig
            $left = (Select-String -Path $orig -Pattern 'ttl09' -EA SilentlyContinue | Measure-Object).Count
            Write-Host ("    sanitized {0}  (ttl09 left={1})" -f $_.Name, $left)
        }
        else {
            Write-Host ("    excluded from build: {0}" -f $_.Name)
        }
    }
}

Write-Host '-- [1b] sanitize loose bundled files --'
foreach ($lf in $looseFiles) {
    if (-not (Test-Path $lf)) { continue }
    $bak = Join-Path $backupRoot ('loose_' + (Split-Path $lf -Leaf))
    Copy-Item $lf $bak -Force
    $journal += [pscustomobject]@{ orig = $lf; backup = $bak }
    $raw = Get-Content $lf -Raw -Encoding UTF8
    $clean = Sanitize-Text $raw
    [System.IO.File]::WriteAllText($lf, $clean, $utf8NoBom)
    $left = (Select-String -Path $lf -Pattern 'ttl09' -EA SilentlyContinue | Measure-Object).Count
    Write-Host ("    sanitized {0}  (ttl09 left={1})" -f (Split-Path $lf -Leaf), $left)
}

try {
    Write-Host ''
    Write-Host ("-- [2] electron-builder build -> " + $OutDir + " --")
    & $builder --win nsis --x64 ("--config.directories.output=" + $OutDir)
    if ($LASTEXITCODE -ne 0) { throw "electron-builder exited with code $LASTEXITCODE" }

    Write-Host ''
    Write-Host '-- artifacts --'
    $outFull = Join-Path $root $OutDir
    Get-ChildItem $outFull -Filter '*.exe' -File | ForEach-Object {
        Write-Host ("    " + $_.FullName + "  (" + [math]::Round($_.Length / 1MB, 2) + " MB)")
    }
    if (Test-Path (Join-Path $outFull 'win-unpacked')) {
        Write-Host '    (win-unpacked created -> portable zip can be regenerated)'
    }
}
finally {
    Write-Host ''
    Write-Host '-- [3] restore originals (always) --'
    foreach ($w in $written) { Remove-Item $w -Force -ErrorAction SilentlyContinue }
    foreach ($j in $journal) {
        try {
            Copy-Item $j.backup $j.orig -Force
            Remove-Item $j.backup -Force -ErrorAction SilentlyContinue
            $c = (Select-String -Path $j.orig -Pattern 'ttl09' -EA SilentlyContinue | Measure-Object).Count
            Write-Host ("    restored {0}  (ttl09={1})" -f (Split-Path $j.orig -Leaf), $c)
        }
        catch {
            Write-Host ("    [WARN] restore failed: {0} <- {1}" -f $j.orig, $j.backup) -ForegroundColor Yellow
        }
    }
    Remove-Item $backupRoot -Recurse -Force -ErrorAction SilentlyContinue
    Write-Host '-- done --'
}
