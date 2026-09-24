#define AppName "codex-usage-hud"
#define AppPublisher "codex-usage-hud contributors"
#define AppURL "https://github.com/fengbuming/codex-usage-hud"
#define AppExeName "codex-hud.exe"

#ifndef AppVersion
  #error AppVersion must be supplied by tools/build_installer.py
#endif

#ifndef SourceExe
  #error SourceExe must be supplied by tools/build_installer.py
#endif

#ifndef ProjectRoot
  #error ProjectRoot must be supplied by tools/build_installer.py
#endif

#ifndef OutputDir
  #define OutputDir AddBackslash(ProjectRoot) + "dist"
#endif

#ifndef OutputBaseFilename
  #define OutputBaseFilename "codex-usage-hud-v" + AppVersion + "-windows-x64-setup"
#endif

[Setup]
AppId={{0F49D5B8-914D-4B10-94F8-5992B5219D1E}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}
AppUpdatesURL={#AppURL}/releases
DefaultDirName={localappdata}\Programs\codex-usage-hud
DefaultGroupName=codex-usage-hud
DisableDirPage=yes
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir={#OutputDir}
OutputBaseFilename={#OutputBaseFilename}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\{#AppExeName}
SetupLogging=yes
CloseApplications=force
RestartApplications=no

[Languages]
Name: "chinesesimp"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加快捷方式："; Flags: unchecked
Name: "startup"; Description: "开机自动启动 HUD daemon"; GroupDescription: "后台运行："; Flags: unchecked

[Files]
Source: "{#SourceExe}"; DestDir: "{app}"; DestName: "{#AppExeName}"; Flags: ignoreversion
Source: "{#SourceExe}"; DestDir: "{tmp}"; DestName: "codex-hud-stop.exe"; Flags: dontcopy
Source: "{#ProjectRoot}\LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#ProjectRoot}\README.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#ProjectRoot}\README_EN.md"; DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist

[Icons]
Name: "{autoprograms}\codex-usage-hud\Codex Usage HUD"; Filename: "{app}\{#AppExeName}"; Parameters: "--daemon"; WorkingDir: "{app}"
Name: "{autoprograms}\codex-usage-hud\Stop Codex Usage HUD"; Filename: "{app}\{#AppExeName}"; Parameters: "--stop"; WorkingDir: "{app}"
Name: "{autoprograms}\codex-usage-hud\Check for Updates"; Filename: "{app}\{#AppExeName}"; Parameters: "--check-update"; WorkingDir: "{app}"
Name: "{autodesktop}\Codex Usage HUD"; Filename: "{app}\{#AppExeName}"; Parameters: "--daemon"; WorkingDir: "{app}"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "codex-usage-hud"; ValueData: """{app}\{#AppExeName}"" --daemon --no-startup-prompt"; Tasks: startup

[Run]
Filename: "{app}\{#AppExeName}"; Parameters: "--daemon"; Description: "启动 Codex Usage HUD"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{app}\{#AppExeName}"; Parameters: "--stop"; Flags: runhidden waituntilterminated; RunOnceId: "StopCodexUsageHud"

[Code]
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ResultCode: Integer;
  Attempt: Integer;
  StopExePath: String;
begin
  Result := '';
  if not CheckForMutexes('Local\codex_usage_hud_single_instance') then
    Exit;

  ExtractTemporaryFile('codex-hud-stop.exe');
  StopExePath := ExpandConstant('{tmp}\codex-hud-stop.exe');
  if not Exec(StopExePath, '--stop', '', SW_HIDE, ewWaitUntilTerminated, ResultCode) then
  begin
    Result := '无法启动旧 HUD 进程的停止程序。';
    Exit;
  end;
  if ResultCode <> 0 then
  begin
    Result := '停止旧 HUD 进程失败。';
    Exit;
  end;
  for Attempt := 1 to 50 do
  begin
    if not CheckForMutexes('Local\codex_usage_hud_single_instance') then
      Exit;
    Sleep(200);
  end;
  Result := '旧 HUD 进程仍在运行，请先退出后重试安装。';
end;
