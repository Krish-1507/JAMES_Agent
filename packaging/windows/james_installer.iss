; JAMES — Inno Setup installer for Windows
; Builds a signed installer from the PyInstaller output (dist\JAMES.exe).
; Usage (in repo root, after `pyinstaller james.spec`):
;   ISCC.exe packaging\windows\james_installer.iss
; Optional signing (Azure Trusted Signing / signtool-compatible):
;   ISCC /DSIGNTOOL_PATH="..." /DSIGN_CERT_SHA1=... packaging\windows\james_installer.iss

#ifndef MyAppVersion
  #define MyAppVersion "1.0.0"
#endif
#ifndef MyAppName
  #define MyAppName "JAMES"
#endif

[Setup]
AppId={{B8A0C64E-9A3F-4F5E-8C2B-1E6D5A4F3B2A}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher=JAMES Contributors
AppPublisherURL=https://github.com/Krish-1507/JAMES_Agent
AppSupportURL=https://github.com/Krish-1507/JAMES_Agent/issues
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=..\..\dist
OutputBaseFilename=JAMES-Setup-{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\JAMES.exe
; Keep the console visible for CLI use; the desktop app also opens from the shortcut.
LicenseFile=..\..\LICENSE

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
Source: "..\..\dist\JAMES.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\.env.example"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\JAMES.exe"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\JAMES.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional icons:"

[Run]
Filename: "{app}\JAMES.exe"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent

[Code]
function InitializeSetup(): Boolean;
begin
  Result := True;
end;
