; 文序 Windows 安装包（Inno Setup 6）
; 用法：ISCC.exe /DMyAppVersion=0.3.0 wensu.iss
#define MyAppName "文序"
#ifndef MyAppVersion
  #define MyAppVersion "0.3.0"
#endif
#define MyAppExeName "Wensu.exe"

[Setup]
AppId={{8B4F2A61-7C1D-4E2A-9F6B-1A2D5C0E9E21}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher=wensu
DefaultDirName={localappdata}\Programs\Wensu
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist\installer
OutputBaseFilename=Wensu-Setup-v{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\{#MyAppExeName}

[Files]
Source: "..\dist\Wensu\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务："; Flags: unchecked

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "立即启动 {#MyAppName}"; Flags: nowait postinstall skipifsilent
