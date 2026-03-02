; ============================================================
; Inno Setup Script for Claude Code Scheduler
; Download Inno Setup from: https://jrsoftware.org/isinfo.php
; ============================================================

#define MyAppName "Claude Code Scheduler"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "Sowrabh Mugi"
#define MyAppURL "https://github.com/sowrabhm/ClaudeCodeScheduler"
#define MyAppExeName "ClaudeCodeScheduler.exe"

[Setup]
AppId={{B8F4A3C1-7E2D-4A6B-9C5F-1D3E7A8B2C4D}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}/issues
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
AllowNoIcons=yes
LicenseFile=LICENSE
OutputDir=Output
OutputBaseFilename=ClaudeCodeScheduler_Setup_{#MyAppVersion}
SetupIconFile=app.ico
Compression=lzma
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"
Name: "startupentry"; Description: "Start automatically with Windows"; GroupDescription: "Startup:"

[Files]
Source: "dist\ClaudeCodeScheduler\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "ClaudeCodeScheduler"; ValueData: """{app}\{#MyAppExeName}"" --background"; Flags: uninsdeletevalue; Tasks: startupentry

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "taskkill"; Parameters: "/F /IM ClaudeCodeScheduler.exe"; Flags: runhidden; RunOnceId: "KillApp"

[Code]
procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssInstall then
  begin
    // Kill running instance before install
    Exec('taskkill', '/F /IM ClaudeCodeScheduler.exe', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  end;
end;

var
  ResultCode: Integer;
