; Windows setup wizard for a PXLVZN app, built by installer/build_windows.ps1:
;   ISCC /DAppName=Tag /DAppVersion=1.2.3 /DAppURL=https://... installer\windows.iss
;
; Installs for the current user into %LOCALAPPDATA%\Programs\<App> (no admin
; prompt). The app updates itself by swapping that folder for a new one, so it
; must live somewhere the user can write, and the uninstaller is kept outside
; it so an update doesn't remove it.

#ifndef AppName
  #error Pass /DAppName=<name>
#endif
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef AppURL
  #define AppURL "https://pxlvzn.in"
#endif
#ifndef SourceDir
  #define SourceDir "..\dist\" + AppName
#endif
#ifndef OutputDir
  #define OutputDir "..\dist"
#endif
#define AppExe AppName + ".exe"

[Setup]
; The app's updater looks for this key (PXLVZN.<App>_is1) to keep the version in
; Settings > Apps current after it updates itself. Don't change it.
AppId=PXLVZN.{#AppName}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=PXLVZN
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}
AppUpdatesURL={#AppURL}
VersionInfoVersion={#AppVersion}
VersionInfoProductName={#AppName}
VersionInfoCompany=PXLVZN
VersionInfoDescription={#AppName} Setup
PrivilegesRequired=lowest
DefaultDirName={localappdata}\Programs\{#AppName}
DisableDirPage=yes
DisableProgramGroupPage=yes
DisableWelcomePage=no
UninstallFilesDir={localappdata}\PXLVZN\Uninstall\{#AppName}
UninstallDisplayName={#AppName}
UninstallDisplayIcon={app}\{#AppExe}
SetupIconFile=..\assets\icon.ico
WizardStyle=modern
WizardSizePercent=100
OutputDir={#OutputDir}
OutputBaseFilename={#AppName}-Setup
Compression=lzma2/max
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
CloseApplications=force
RestartApplications=no
SetupLogging=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[InstallDelete]
; Reinstalling over an older version: clear the old build's libraries first.
Type: filesandordirs; Name: "{app}\_internal"
Type: filesandordirs; Name: "{app}.old"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{userprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"; WorkingDir: "{app}"
Name: "{userdesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{sys}\taskkill.exe"; Parameters: "/f /t /im {#AppExe}"; Flags: runhidden; RunOnceId: "CloseApp"

[UninstallDelete]
; Updates replace the app folder with files the installer never saw, so remove it whole.
Type: filesandordirs; Name: "{app}"
Type: filesandordirs; Name: "{app}.old"
