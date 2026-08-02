; ── DAON Agent System NSIS Installer Script ──
; electron-builder automatically includes this file when
; "nsis.include" is set in package.json → build.win.nsis.include
;
; [설치 멈춤 근본 대책]
; 진행률 ~10% 멈춤의 주원인은 1.1GB server.exe 가 포함된 7z 솔리드
; 아카이브 압축 해제 단계에서 진행률 갱신이 멈추고, 클라우드 동기화
; (ProtonDrive / OneDrive 등)·Windows Defender 가 부분 파일을 잠그는 것이다.
; 1) package.json 의 nsis.useZip = true 로 압축 방식을 zip 스트림으로 전환
;    (7z 솔리드 해제 병목/부분파일 잠금 윈도우 제거)
; 2) customInit / customCheckAppRunning 에서 잔여 프로세스를 강제 종료해 파일 잠금 해소
;    (멈춤 재발 시 인스톨러를 `...Setup.exe /LOG=%TEMP\daon-install.log` 로 실행하면 기록됨)

!macro customInit
  ; ── Force-kill running processes BEFORE installation ──
  nsExec::Exec '"$SYSDIR\taskkill.exe" /F /IM "DAON Agent System.exe" /T'
  nsExec::Exec '"$SYSDIR\taskkill.exe" /F /IM "server.exe" /T'
  Sleep 2000
!macroend

!macro customCheckAppRunning
  ; Bypass electron-builder's default process check
  nsExec::Exec '"$SYSDIR\taskkill.exe" /F /IM "DAON Agent System.exe" /T'
  nsExec::Exec '"$SYSDIR\taskkill.exe" /F /IM "server.exe" /T'
  Sleep 2000
!macroend

!macro customInstall
!macroend

!macro customUnInit
  ; Force-kill before uninstall too
  nsExec::Exec '"$SYSDIR\taskkill.exe" /F /IM "DAON Agent System.exe" /T'
  nsExec::Exec '"$SYSDIR\taskkill.exe" /F /IM "server.exe" /T'
  Sleep 1000
!macroend
