; Inno Setup script - built by build.ps1 (pass /DAppVersion=x.y.z)
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#define AppName "Res & Vibrance Switch"
#define AppExe "ResVibranceSwitch.exe"

[Setup]
AppId={{8F3C2A51-6B7E-4F0A-9D2C-5E1B7A4C9D10}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppName}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
; Per-user install: no admin prompt, installs to %LOCALAPPDATA%\Programs
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=dist
OutputBaseFilename=ResVibranceSwitch-Setup-{#AppVersion}
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
WizardStyle=modern
Compression=lzma2/max
SolidCompression=yes
CloseApplications=yes
RestartApplications=no

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; Flags: unchecked
Name: "startup"; Description: "Start {#AppName} with &Windows"

[Files]
Source: "dist-installer\ResVibranceSwitch\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "ResVibranceSwitch"; ValueData: """{app}\{#AppExe}"" --minimized"; Tasks: startup

[Run]
Filename: "{app}\{#AppExe}"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
; Quit the app (it restores desktop settings on a normal quit; a forced stop is recovered on next start)
Filename: "{sys}\taskkill.exe"; Parameters: "/im {#AppExe} /f"; Flags: runhidden; RunOnceId: "StopApp"
; The app can also add itself to startup from its settings, so always remove that entry
Filename: "{sys}\reg.exe"; Parameters: "delete HKCU\Software\Microsoft\Windows\CurrentVersion\Run /v ResVibranceSwitch /f"; Flags: runhidden; RunOnceId: "RemoveStartup"
