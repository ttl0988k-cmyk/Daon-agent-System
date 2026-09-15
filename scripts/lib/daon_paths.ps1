# ─────────────────────────────────────────────────────────────
# daon_paths.ps1 — DAON 배포 경로 "단일 정의" (scripts/* 공용)
#
# 왜 필요한가 (문제의 근원):
#   electron-builder 의 productName 은 공백 포함 "DAON Agent System" 이지만,
#   실제 설치 폴더명은 하이픈/소문자 "daon-agent-system" 이다.
#   이 불일치 때문에 아래 스크립트들이 서로 다른 폴더를 가리켜,
#   빌드 산출물이 "여기저기 흩어진" 것처럼 보였다.
#     · scripts/after-pack.js            : 공백 경로로 app.asar 자동동기화 → 조용히 실패
#     · scripts/sync_to_installed.ps1    : 공백 경로만 보고 "설치본 없음" → exit 1
#     · install-portable.ps1             : 공백 경로로 "또 다른" 설치본 생성
#
# 사용법 (다른 스크립트에서 dot-source):
#   . (Join-Path $PSScriptRoot 'lib\daon_paths.ps1')
#   $appDir = Resolve-DaonInstalledDir           # 실제 존재하는 설치본 폴더 or $null
#   $ports  = Resolve-DaonDeployTargets          # 배포 대상 resources 폴더 목록
# ─────────────────────────────────────────────────────────────

# electron-builder productName (공백 버전) — 바로가기/프로세스 이름 기준
$script:DaonProductName = 'DAON Agent System'
# 실제 설치 폴더명 (하이픈/소문자) — 바로가기 실제 대상 기준
$script:DaonInstallFolder = 'daon-agent-system'
# 포터블 배포 위치
$script:DaonPortableDir = 'C:\daon\DAON-Portable'

function Get-DaonInstallCandidates {
    <# 설치본 폴더 후보를 우선순위대로 반환 (존재 여부 무관). #>
    param([string]$Explicit)
    $prog = Join-Path $env:LOCALAPPDATA 'Programs'
    $list = @()
    if ($Explicit) { $list += $Explicit }
    $list += (Join-Path $prog $script:DaonInstallFolder)   # 하이픈 (실제)
    $list += (Join-Path $prog $script:DaonProductName)     # 공백 (레거시)
    return $list
}

function Resolve-DaonInstalledDir {
    <# 실제로 존재하는 설치본 폴더를 반환. 없으면 $null. #>
    param([string]$Explicit)
    foreach ($c in (Get-DaonInstallCandidates -Explicit $Explicit)) {
        if (Test-Path $c) { return $c }
    }
    return $null
}

function Resolve-DaonDeployTargets {
    <#
      "배포된(실행 중일 수 있는) 사본" 의 resources 폴더 목록을 반환.
      = 설치본(하이픈) + 포터블. 각 항목: [pscustomobject]@{ Name; Root; Resources }
      존재하는 것만 포함한다.
    #>
    param([string]$Explicit)
    $out = @()
    $installed = Resolve-DaonInstalledDir -Explicit $Explicit
    if ($installed) {
        $out += [pscustomobject]@{
            Name      = 'installed'
            Root      = $installed
            Resources = (Join-Path $installed 'resources')
        }
    }
    if (Test-Path $script:DaonPortableDir) {
        $out += [pscustomobject]@{
            Name      = 'portable'
            Root      = $script:DaonPortableDir
            Resources = (Join-Path $script:DaonPortableDir 'resources')
        }
    }
    return $out
}

function Get-DaonMainExe {
    <# 앱 실행 파일 경로 (설치본/포터블 공통: <root>\DAON Agent System.exe) #>
    param([Parameter(Mandatory = $true)][string]$Root)
    return (Join-Path $Root ($script:DaonProductName + '.exe'))
}
