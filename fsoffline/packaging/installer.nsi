; FS-Platform desktop edition — Windows installer (NSIS 3, Unicode).
; Built by fsoffline/packaging/build_windows.py, which passes VERSION, STAGE (the bundle: python\, ui\, icon, notices),
; REDIST (folder with the LibreOffice MSI and the VC++ runtime), LO_MSI, VCREDIST and OUTFILE.
;
; Silent use:  FS-Platform-Setup-<v>.exe /S             install or upgrade (data is kept)
;              "Uninstall FS-Platform.exe" /S             uninstall, keep the data
;              "Uninstall FS-Platform.exe" /S /DELETEDATA uninstall and delete all data

Unicode true
ManifestDPIAware true
RequestExecutionLevel admin
SetCompressor lzma

!include "MUI2.nsh"
!include "LogicLib.nsh"
!include "x64.nsh"
!include "WinVer.nsh"
!include "FileFunc.nsh"
!include "nsDialogs.nsh"

!define PRODUCT "FS-Platform"
!define PUBLISHER "FS-Platform"
!define UNINST_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\FS-Platform"
!define RUN_KEY "Software\Microsoft\Windows\CurrentVersion\Run"
!define USERS_GROUP "FS-Platform Users"
!define PORT "8746"
!define APP_ARGS "-X utf8 -m fsoffline"

Name "${PRODUCT}"
OutFile "${OUTFILE}"
InstallDir "$PROGRAMFILES64\FS-Platform"
InstallDirRegKey HKLM "Software\FS-Platform" "InstallDir"
BrandingText "${PRODUCT} ${VERSION}"
VIProductVersion "${VERSION}.0"
VIAddVersionKey "ProductName" "${PRODUCT}"
VIAddVersionKey "CompanyName" "${PUBLISHER}"
VIAddVersionKey "FileDescription" "${PRODUCT} setup"
VIAddVersionKey "FileVersion" "${VERSION}"
VIAddVersionKey "ProductVersion" "${VERSION}"
VIAddVersionKey "LegalCopyright" "${PUBLISHER}"

!define MUI_ICON "${STAGE}/FS-Platform.ico"
!define MUI_UNICON "${STAGE}/FS-Platform.ico"
!define MUI_ABORTWARNING
!define MUI_WELCOMEPAGE_TEXT "This installs ${PRODUCT} ${VERSION} on this PC: the application, its Python runtime and libraries, the web interface, and LibreOffice for PDFs.$\r$\n$\r$\n${PRODUCT} runs on this PC only and opens in your web browser; nothing is reachable from the network.$\r$\n$\r$\nAn existing installation is upgraded and its data kept."
!define MUI_FINISHPAGE_TITLE "${PRODUCT} is installed"
!define MUI_FINISHPAGE_TEXT "Start ${PRODUCT} from its shortcut on the desktop or in the Start menu. The first start opens the setup page in your browser, where you create your firm and the first administrator.$\r$\n$\r$\nOther Windows accounts on this PC can use ${PRODUCT} once they are added with ${PRODUCT} Recovery (Start menu)."

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_COMPONENTS
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
UninstPage custom un.DataPage un.DataPageLeave
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "English"

Var DataDir
Var Soffice
Var DeleteData
Var DeleteDataBox

; ---------------------------------------------------------------------------------------------------------------- helpers
!macro StopRunning
  ; a running copy (in any Windows account) is asked to stop; anything still running from the install folder is ended
  ${If} ${FileExists} "$INSTDIR\python\python.exe"
    DetailPrint "Stopping ${PRODUCT} if it is running..."
    nsExec::ExecToLog `"$INSTDIR\python\python.exe" ${APP_ARGS} --data-dir "$DataDir" stop`
    Pop $0
  ${EndIf}
  nsExec::ExecToLog `"$SYSDIR\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -NonInteractive -Command "Get-Process | Where-Object { $$_.Path -like '$INSTDIR\*' } | Stop-Process -Force -ErrorAction SilentlyContinue"`
  Pop $0
!macroend

Function FoundSoffice
  StrCpy $Soffice $R9
  Push "StopLocate"
FunctionEnd

Function .onInit
  ${IfNot} ${RunningX64}
    MessageBox MB_ICONSTOP "${PRODUCT} needs 64-bit Windows 10 or 11." /SD IDOK
    Abort
  ${EndIf}
  ${IfNot} ${AtLeastWin10}
    MessageBox MB_ICONSTOP "${PRODUCT} needs Windows 10 or 11." /SD IDOK
    Abort
  ${EndIf}
  SetRegView 64
  SetShellVarContext all
  StrCpy $DataDir "$APPDATA\FSPlatform"  ; all users' application data = C:\ProgramData
FunctionEnd

; ---------------------------------------------------------------------------------------------------------------- install
Section "${PRODUCT} (required)" SecMain
  SectionIn RO
  SetRegView 64
  SetShellVarContext all
  !insertmacro StopRunning

  ; program files are replaced on every install or upgrade; the data folder is never touched here
  RMDir /r "$INSTDIR\python"
  RMDir /r "$INSTDIR\ui"
  RMDir /r "$INSTDIR\LibreOffice"
  SetOutPath "$INSTDIR"
  File "${STAGE}/FS-Platform.ico"
  File "${STAGE}/THIRD-PARTY-NOTICES.txt"
  File /r "${STAGE}/python"
  File /r "${STAGE}/ui"
  WriteUninstaller "$INSTDIR\Uninstall FS-Platform.exe"

  ; Microsoft Visual C++ runtime: the Python runtime, its native libraries and LibreOffice need it
  ReadRegDWORD $0 HKLM "SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64" "Installed"
  ReadRegDWORD $1 HKLM "SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64" "Minor"
  ${If} $0 <> 1
  ${OrIf} $1 < 40
    SetOutPath "$PLUGINSDIR"
    SetCompress off
    File "${REDIST}/${VCREDIST}"
    SetCompress auto
    DetailPrint "Installing the Microsoft Visual C++ runtime..."
    ExecWait `"$PLUGINSDIR\${VCREDIST}" /install /quiet /norestart` $0
    ${If} $0 <> 0
    ${AndIf} $0 <> 1638
    ${AndIf} $0 <> 3010
      MessageBox MB_ICONSTOP "The Microsoft Visual C++ runtime could not be installed (error $0)." /SD IDOK
      Abort
    ${EndIf}
  ${EndIf}

  ; LibreOffice as an administrative image: not registered as a separate product, no file associations, and it
  ; cannot change a LibreOffice the PC already has
  SetOutPath "$PLUGINSDIR"
  SetCompress off
  File "${REDIST}/${LO_MSI}"
  SetCompress auto
  DetailPrint "Unpacking LibreOffice (the PDF engine; this takes a minute or two)..."
  StrCpy $2 0
  lo_retry:
    ExecWait `"$SYSDIR\msiexec.exe" /a "$PLUGINSDIR\${LO_MSI}" /qn TARGETDIR="$INSTDIR\LibreOffice"` $0
    ${If} $0 = 1618
    ${AndIf} $2 < 20
      IntOp $2 $2 + 1
      DetailPrint "Another installation is running; waiting..."
      Sleep 15000
      Goto lo_retry
    ${EndIf}
  ${If} $0 <> 0
    MessageBox MB_ICONSTOP "LibreOffice could not be unpacked (Windows Installer error $0)." /SD IDOK
    Abort
  ${EndIf}
  Delete "$PLUGINSDIR\${LO_MSI}"
  StrCpy $Soffice ""
  ${Locate} "$INSTDIR\LibreOffice" "/L=F /M=soffice.exe" "FoundSoffice"
  ${If} $Soffice == ""
    MessageBox MB_ICONSTOP "LibreOffice was unpacked but soffice.exe was not found." /SD IDOK
    Abort
  ${EndIf}
  DetailPrint "LibreOffice: $Soffice"

  ; the machine-wide data folder: SYSTEM and Administrators (well-known SIDs, so any Windows language works) with full
  ; control; the "FS-Platform Users" group and the account installing now may change it; nobody else may open it
  CreateDirectory "$DataDir"
  nsExec::ExecToLog `"$SYSDIR\net.exe" localgroup "${USERS_GROUP}" /add /comment:"Windows accounts that may use FS-Platform on this PC"`
  Pop $0
  ReadEnvStr $3 USERNAME
  ReadEnvStr $4 USERDOMAIN
  nsExec::ExecToLog `"$SYSDIR\net.exe" localgroup "${USERS_GROUP}" "$4\$3" /add`
  Pop $0
  nsExec::ExecToLog `"$SYSDIR\icacls.exe" "$DataDir" /inheritance:r /grant:r *S-1-5-18:(OI)(CI)F *S-1-5-32-544:(OI)(CI)F "${USERS_GROUP}:(OI)(CI)M" "$4\$3:(OI)(CI)M"`
  Pop $0
  ${If} $0 <> 0
    DetailPrint "Warning: could not set the permissions of $DataDir (icacls $0)"
  ${EndIf}

  ; install configuration, read by the application (Program Files: only administrators can change it)
  Delete "$INSTDIR\fsplatform.ini"
  WriteINIStr "$INSTDIR\fsplatform.ini" "fsplatform" "version" "${VERSION}"
  WriteINIStr "$INSTDIR\fsplatform.ini" "fsplatform" "data_dir" "$DataDir"
  WriteINIStr "$INSTDIR\fsplatform.ini" "fsplatform" "ui_dir" "$INSTDIR\ui"
  WriteINIStr "$INSTDIR\fsplatform.ini" "fsplatform" "soffice" "$Soffice"
  WriteINIStr "$INSTDIR\fsplatform.ini" "fsplatform" "icon" "$INSTDIR\FS-Platform.ico"
  WriteINIStr "$INSTDIR\fsplatform.ini" "fsplatform" "port" "${PORT}"

  ; Start menu (shortcuts start in the install folder)
  SetOutPath "$INSTDIR"
  CreateDirectory "$SMPROGRAMS\${PRODUCT}"
  CreateShortcut "$SMPROGRAMS\${PRODUCT}\${PRODUCT}.lnk" "$INSTDIR\python\pythonw.exe" "${APP_ARGS} desktop" "$INSTDIR\FS-Platform.ico" 0 SW_SHOWNORMAL "" "Start ${PRODUCT} and open it in your browser"
  CreateShortcut "$SMPROGRAMS\${PRODUCT}\${PRODUCT} Recovery.lnk" "$INSTDIR\python\python.exe" "${APP_ARGS} recover" "$INSTDIR\FS-Platform.ico" 0 SW_SHOWNORMAL "" "Reset an administrator's password or authenticator, allow a Windows account, restore a backup"
  CreateShortcut "$SMPROGRAMS\${PRODUCT}\Uninstall ${PRODUCT}.lnk" "$INSTDIR\Uninstall FS-Platform.exe"
  CreateShortcut "$SMPROGRAMS\${PRODUCT}\Third-party notices.lnk" "$INSTDIR\THIRD-PARTY-NOTICES.txt"

  ; Add/Remove Programs
  WriteRegStr HKLM "Software\FS-Platform" "InstallDir" "$INSTDIR"
  WriteRegStr HKLM "${UNINST_KEY}" "DisplayName" "${PRODUCT}"
  WriteRegStr HKLM "${UNINST_KEY}" "DisplayVersion" "${VERSION}"
  WriteRegStr HKLM "${UNINST_KEY}" "Publisher" "${PUBLISHER}"
  WriteRegStr HKLM "${UNINST_KEY}" "DisplayIcon" "$INSTDIR\FS-Platform.ico"
  WriteRegStr HKLM "${UNINST_KEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKLM "${UNINST_KEY}" "UninstallString" `"$INSTDIR\Uninstall FS-Platform.exe"`
  WriteRegStr HKLM "${UNINST_KEY}" "QuietUninstallString" `"$INSTDIR\Uninstall FS-Platform.exe" /S`
  WriteRegDWORD HKLM "${UNINST_KEY}" "NoModify" 1
  WriteRegDWORD HKLM "${UNINST_KEY}" "NoRepair" 1
  ${GetSize} "$INSTDIR" "/S=0K" $0 $1 $2
  IntFmt $0 "0x%08X" $0
  WriteRegDWORD HKLM "${UNINST_KEY}" "EstimatedSize" "$0"
SectionEnd

Section "Desktop shortcut" SecDesktop
  SetShellVarContext all
  SetOutPath "$INSTDIR"
  CreateShortcut "$DESKTOP\${PRODUCT}.lnk" "$INSTDIR\python\pythonw.exe" "${APP_ARGS} desktop" "$INSTDIR\FS-Platform.ico" 0 SW_SHOWNORMAL "" "Start ${PRODUCT} and open it in your browser"
SectionEnd

Section "Start ${PRODUCT} when you sign in to Windows" SecAutostart
  SetRegView 64
  WriteRegStr HKLM "${RUN_KEY}" "${PRODUCT}" `"$INSTDIR\python\pythonw.exe" ${APP_ARGS} desktop --no-browser`
SectionEnd

!insertmacro MUI_FUNCTION_DESCRIPTION_BEGIN
  !insertmacro MUI_DESCRIPTION_TEXT ${SecMain} "The application, its Python runtime and libraries, the web interface and LibreOffice."
  !insertmacro MUI_DESCRIPTION_TEXT ${SecDesktop} "A shortcut on the desktop of every user of this PC."
  !insertmacro MUI_DESCRIPTION_TEXT ${SecAutostart} "Runs ${PRODUCT} in the background (tray icon) whenever someone signs in, so it is ready in the browser."
!insertmacro MUI_FUNCTION_DESCRIPTION_END

; ---------------------------------------------------------------------------------------------------------------- uninstall
Function un.onInit
  SetRegView 64
  SetShellVarContext all
  StrCpy $DataDir "$APPDATA\FSPlatform"
  StrCpy $DeleteData 0
  ${un.GetParameters} $0
  ClearErrors
  ${un.GetOptions} $0 "/DELETEDATA" $1
  ${IfNot} ${Errors}
    StrCpy $DeleteData 1
  ${EndIf}
FunctionEnd

Function un.DataPage
  nsDialogs::Create 1018
  Pop $0
  ${NSD_CreateLabel} 0 0 100% 40u "${PRODUCT}'s data — the database, uploaded workbooks, PDFs, backups and keys — is kept in $DataDir, so a later installation finds it again."
  Pop $0
  ${NSD_CreateCheckbox} 0 48u 100% 12u "Also delete all ${PRODUCT} data on this PC (this cannot be undone)"
  Pop $DeleteDataBox
  nsDialogs::Show
FunctionEnd

Function un.DataPageLeave
  ${NSD_GetState} $DeleteDataBox $0
  ${If} $0 == ${BST_CHECKED}
    MessageBox MB_YESNO|MB_ICONEXCLAMATION "Delete all ${PRODUCT} data, including the backups in $DataDir\backups? This cannot be undone." IDYES +2
    Abort
    StrCpy $DeleteData 1
  ${EndIf}
FunctionEnd

Section "Uninstall"
  SetRegView 64
  SetShellVarContext all
  !insertmacro StopRunning
  Delete "$DESKTOP\${PRODUCT}.lnk"
  RMDir /r "$SMPROGRAMS\${PRODUCT}"
  DeleteRegValue HKLM "${RUN_KEY}" "${PRODUCT}"
  RMDir /r "$INSTDIR\python"
  RMDir /r "$INSTDIR\ui"
  RMDir /r "$INSTDIR\LibreOffice"
  Delete "$INSTDIR\FS-Platform.ico"
  Delete "$INSTDIR\THIRD-PARTY-NOTICES.txt"
  Delete "$INSTDIR\fsplatform.ini"
  Delete "$INSTDIR\Uninstall FS-Platform.exe"
  RMDir "$INSTDIR"
  DeleteRegKey HKLM "${UNINST_KEY}"
  DeleteRegKey HKLM "Software\FS-Platform"
  ${If} $DeleteData = 1
    ; stored files are read-only (immutable versions): clear the attribute so the folder can be removed
    nsExec::ExecToLog `"$SYSDIR\attrib.exe" -R "$DataDir\*" /S /D`
    Pop $0
    RMDir /r "$DataDir"
    nsExec::ExecToLog `"$SYSDIR\net.exe" localgroup "${USERS_GROUP}" /delete`
    Pop $0
  ${EndIf}
SectionEnd
