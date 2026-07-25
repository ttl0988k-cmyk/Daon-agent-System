; ── DAON Agent System NSIS Installer Script ──
; electron-builder automatically includes this file when
; "nsis.include" is set in package.json → build.win.nsis.include

!macro customInit
  ; ── Force-kill running processes BEFORE installation ──
  nsExec::Exec '"$SYSDIR\taskkill.exe" /F /IM "DAON Agent System.exe" /T'
  nsExec::Exec '"$SYSDIR\taskkill.exe" /F /IM "server.exe" /T'
  Sleep 1500
!macroend

!macro customCheckAppRunning
  ; Bypass electron-builder's default process check
  nsExec::Exec '"$SYSDIR\taskkill.exe" /F /IM "DAON Agent System.exe" /T'
  nsExec::Exec '"$SYSDIR\taskkill.exe" /F /IM "server.exe" /T'
  Sleep 1500
!macroend

!macro customInstall
!macroend

!macro customUnInit
  ; Force-kill before uninstall too
  nsExec::Exec '"$SYSDIR\taskkill.exe" /F /IM "DAON Agent System.exe" /T'
  nsExec::Exec '"$SYSDIR\taskkill.exe" /F /IM "server.exe" /T'
  Sleep 1000
!macroend
