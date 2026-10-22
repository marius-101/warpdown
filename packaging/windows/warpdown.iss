; Inno Setup script. Build with:  iscc /DAppVersion=0.8.0 packaging\windows\warpdown.iss  (after build.ps1)
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{6C2B4E5A-0D1F-4B8E-9F3A-7A1D5C0E2B64}
AppName=Warpdown
AppVersion={#AppVersion}
AppPublisher=marius-101
AppPublisherURL=https://github.com/marius-101/warpdown
DefaultDirName={autopf}\Warpdown
DefaultGroupName=Warpdown
PrivilegesRequired=lowest
OutputDir=..\..\dist
OutputBaseFilename=warpdown-setup-{#AppVersion}
SetupIconFile=..\..\warpdown\assets\warpdown.ico
UninstallDisplayIcon={app}\warpdown.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked
Name: "magnet"; Description: "Open magnet links with Warpdown"; Flags: unchecked

[Files]
Source: "..\..\build\windows\warpdown\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\Warpdown"; Filename: "{app}\warpdown.exe"
Name: "{autodesktop}\Warpdown"; Filename: "{app}\warpdown.exe"; Tasks: desktopicon

[Registry]
; Firefox finds the browser connector through this key (the app also keeps it up to date on every start)
Root: HKCU; Subkey: "Software\Mozilla\NativeMessagingHosts\org.warpdown.host"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Google\Chrome\NativeMessagingHosts\org.warpdown.host"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Microsoft\Edge\NativeMessagingHosts\org.warpdown.host"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\BraveSoftware\Brave-Browser\NativeMessagingHosts\org.warpdown.host"; Flags: uninsdeletekey
; .torrent files: Warpdown appears in "Open with" (it does not take over the default)
Root: HKCU; Subkey: "Software\Classes\Warpdown.torrent"; ValueType: string; ValueData: "Torrent file"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\Warpdown.torrent\shell\open\command"; ValueType: string; ValueData: """{app}\warpdown.exe"" ""%1"""
Root: HKCU; Subkey: "Software\Classes\.torrent\OpenWithProgids"; ValueType: string; ValueName: "Warpdown.torrent"; ValueData: ""; Flags: uninsdeletevalue
Root: HKCU; Subkey: "Software\Classes\magnet"; ValueType: string; ValueData: "URL:Magnet link"; Flags: uninsdeletekey; Tasks: magnet
Root: HKCU; Subkey: "Software\Classes\magnet"; ValueName: "URL Protocol"; ValueType: string; ValueData: ""; Tasks: magnet
Root: HKCU; Subkey: "Software\Classes\magnet\shell\open\command"; ValueType: string; ValueData: """{app}\warpdown.exe"" ""%1"""; Tasks: magnet

[Run]
Filename: "{app}\warpdown.exe"; Description: "Start Warpdown"; Flags: nowait postinstall skipifsilent
