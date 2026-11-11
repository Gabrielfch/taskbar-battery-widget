; Inno Setup script. Compile with: ISCC.exe /DAppVersion=1.3.0 packaging\installer.iss
#ifndef AppVersion
  #define AppVersion "1.3.0"
#endif
#define AppName "Taskbar Battery Widget"
#define AppExe "TaskbarBatteryWidget.exe"

[Setup]
AppId={{7B1E4C52-3A9D-4F68-9C0E-5D2A8F6B13E7}
AppName={#AppName}
AppVersion={#AppVersion}
VersionInfoVersion={#AppVersion}
DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=build\installer
OutputBaseFilename=TaskbarBatteryWidget-Setup-{#AppVersion}
SetupIconFile=app.ico
UninstallDisplayIcon={app}\{#AppExe}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes
RestartApplications=no
; Never restore the tasks ticked in a previous run: the startup task default is derived from the
; real current state in InitializeWizard (below), so updating cannot silently re-enable a disabled entry.
UsePreviousTasks=no

[Languages]
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[CustomMessages]
spanish.StartWithWindows=Iniciar con Windows
english.StartWithWindows=Start with Windows

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "startup"; Description: "{cm:StartWithWindows}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "build\dist\TaskbarBatteryWidget\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Registry]
; Same value name and format as startup.py (VALUE_NAME, quoted frozen exe path).
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "TaskbarBatteryWidget"; ValueData: """{app}\{#AppExe}"""; Tasks: startup; Flags: uninsdeletevalue
; Ticking the task is an explicit opt-in: also clear a "disabled" flag left by Task Manager / Settings.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run"; ValueType: binary; ValueName: "TaskbarBatteryWidget"; ValueData: "02 00 00 00 00 00 00 00 00 00 00 00"; Tasks: startup; Flags: uninsdeletevalue

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{sys}\taskkill.exe"; Parameters: "/F /IM {#AppExe}"; Flags: runhidden; RunOnceId: "KillApp"

[UninstallDelete]
; User config in %APPDATA%\TaskbarBatteryWidget is intentionally left untouched.
Type: filesandordirs; Name: "{app}"

[Code]
const
  RunKey = 'Software\Microsoft\Windows\CurrentVersion\Run';
  ApprovedKey = 'Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run';

{ True when the Run value points into this install dir and Task Manager has not disabled it }
function StartupCurrentlyEnabled: Boolean;
var
  V: String;
  B: AnsiString;
begin
  Result := False;
  if RegQueryStringValue(HKEY_CURRENT_USER, RunKey, 'TaskbarBatteryWidget', V) then
    if Pos(Lowercase(WizardDirValue), Lowercase(V)) > 0 then
    begin
      Result := True;
      if RegQueryBinaryValue(HKEY_CURRENT_USER, ApprovedKey, 'TaskbarBatteryWidget', B) then
        if (Length(B) > 0) and ((Ord(B[1]) and 1) <> 0) then
          Result := False;
    end;
end;

procedure InitializeWizard;
begin
  { tick "Start with Windows" only if it is genuinely on right now (update keeps it; a disabled one stays off) }
  if StartupCurrentlyEnabled then
    WizardSelectTasks('startup');
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  V: String;
begin
  if CurUninstallStep = usPostUninstall then
    { only remove the Run value if it points at this installation }
    if RegQueryStringValue(HKEY_CURRENT_USER, 'Software\Microsoft\Windows\CurrentVersion\Run', 'TaskbarBatteryWidget', V) then
      if Pos(Lowercase(ExpandConstant('{app}')), Lowercase(V)) > 0 then
        RegDeleteValue(HKEY_CURRENT_USER, 'Software\Microsoft\Windows\CurrentVersion\Run', 'TaskbarBatteryWidget');
end;
