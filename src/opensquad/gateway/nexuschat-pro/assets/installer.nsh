!macro customCheckAppRunning
  ; Override electron-builder's default CHECK_APP_RUNNING.
  ; The default only kills OpenSquad.exe (Electron main process), but the
  ; backend spawns run.exe children (gateway, launcher, agents, plugin
  ; services) that hold file locks on $INSTDIR. Without killing them,
  ; NSIS shows "无法关闭" and the user must reboot to install.
  Push $0
  Push $1

  check_run_exe:
    nsExec::ExecToStack `taskkill /F /IM "run.exe" /T 2>nul`
    Pop $0  ; exit code
    Pop $1  ; output
    ${if} $0 == 0
      DetailPrint "Killed lingering run.exe processes"
      Sleep 1000
      Goto check_run_exe
    ${endIf}

  check_opensquad:
    nsExec::ExecToStack `taskkill /F /IM "${PRODUCT_FILENAME}.exe" /T 2>nul`
    Pop $0
    Pop $1
    ${if} $0 == 0
      DetailPrint "Killed lingering ${PRODUCT_FILENAME}.exe processes"
      Sleep 1000
      Goto check_opensquad
    ${endIf}

  ; Wait for file handles to fully release
  Sleep 2000
  Pop $1
  Pop $0
!macroend

!macro customInstall
  ; After copying app files, download/configure the bundled Agent Python runtime
  ; with the same Hermes-style progress UI (OpenSquad.exe --setup-runtime).
  DetailPrint "Installing OpenSquad Agent Python runtime..."
  ExecWait '"$INSTDIR\${PRODUCT_FILENAME}.exe" --setup-runtime' $0
  ${If} $0 != 0
    MessageBox MB_ICONEXCLAMATION "Agent runtime setup did not complete (exit $0).$\nYou can retry on first launch."
  ${EndIf}

  ; Auto-update path: after a silent (/S) install, relaunch the freshly
  ; installed app. customCheckAppRunning already killed the previous app and
  ; its backend (run.exe) before file copy, so ports 9555/9600 are free for
  ; the new instance — no "address already in use" on first boot. Interactive
  ; installs keep the standard "Run OpenSquad" finish-page checkbox instead.
  ${If} ${Silent}
    ExecShell open "$INSTDIR\${PRODUCT_FILENAME}.exe"
  ${EndIf}
!macroend
