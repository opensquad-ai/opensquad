!macro customInstall
  ; After copying app files, download/configure the bundled Agent Python runtime
  ; with the same Hermes-style progress UI (OpenSquad.exe --setup-runtime).
  DetailPrint "Installing OpenSquad Agent Python runtime..."
  ExecWait '"$INSTDIR\${PRODUCT_FILENAME}.exe" --setup-runtime' $0
  ${If} $0 != 0
    MessageBox MB_ICONEXCLAMATION "Agent runtime setup did not complete (exit $0).$\nYou can retry on first launch."
  ${EndIf}
!macroend
