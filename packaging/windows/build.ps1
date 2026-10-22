# Builds the Windows program into build\windows\warpdown (run from the project root, Python + requirements installed).
$ErrorActionPreference = "Stop"
$root = (Resolve-Path "$PSScriptRoot\..\..").Path
Set-Location $root
$out = "$root\build\windows"
Remove-Item -Recurse -Force $out, "$root\build\pyi" -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force $out, "$root\build\tools" | Out-Null

# icon
python -c "from PySide6.QtGui import QGuiApplication, QImage, QPainter; from PySide6.QtSvg import QSvgRenderer; from PySide6.QtCore import Qt; a=QGuiApplication([]); r=QSvgRenderer('warpdown/assets/warpdown.svg'); im=QImage(256,256,QImage.Format_ARGB32); im.fill(Qt.transparent); p=QPainter(im); r.render(p); p.end(); im.save('warpdown/assets/warpdown.ico')"

# the program itself (no console window) and the browser connector (needs stdin/stdout)
python -m PyInstaller --noconfirm --windowed --name warpdown --icon "$root\warpdown\assets\warpdown.ico" `
  --distpath $out --workpath "$root\build\pyi" --specpath "$root\build\pyi" --paths $root `
  --add-data "$root\warpdown\assets;warpdown\assets" --add-data "$root\extension;extension" `
  packaging\windows\run_warpdown.py
if ($LASTEXITCODE -ne 0 -or -not (Test-Path "$out\warpdown\warpdown.exe")) { throw "PyInstaller failed for warpdown.exe" }
python -m PyInstaller --noconfirm --onefile --console --name warpdown-host --icon "$root\warpdown\assets\warpdown.ico" `
  --distpath "$out\warpdown" --workpath "$root\build\pyi" --specpath "$root\build\pyi" --paths $root `
  native\warpdown_host.py
if ($LASTEXITCODE -ne 0 -or -not (Test-Path "$out\warpdown\warpdown-host.exe")) { throw "PyInstaller failed for warpdown-host.exe" }

# helper programs: yt-dlp (video sites), aria2 (torrents), ffmpeg (joining video and audio)
$tools = "$root\build\tools"
Invoke-WebRequest "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe" -OutFile "$out\warpdown\yt-dlp.exe"
Invoke-WebRequest "https://github.com/aria2/aria2/releases/download/release-1.37.0/aria2-1.37.0-win-64bit-build1.zip" -OutFile "$tools\aria2.zip"
Expand-Archive "$tools\aria2.zip" "$tools\aria2" -Force
Copy-Item (Get-ChildItem "$tools\aria2" -Recurse -Filter aria2c.exe | Select-Object -First 1).FullName "$out\warpdown\aria2c.exe"
Invoke-WebRequest "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-lgpl.zip" -OutFile "$tools\ffmpeg.zip"
Expand-Archive "$tools\ffmpeg.zip" "$tools\ffmpeg" -Force
Copy-Item (Get-ChildItem "$tools\ffmpeg" -Recurse -Filter ffmpeg.exe | Select-Object -First 1).FullName "$out\warpdown\ffmpeg.exe"
Copy-Item "$root\LICENSE" "$out\warpdown\LICENSE.txt"

# portable zip
$version = (python -c "from warpdown import __version__; print(__version__)").Trim()
Compress-Archive -Path "$out\warpdown" -DestinationPath "$root\dist\warpdown-windows-portable-$version.zip" -Force
Write-Host "Built version $version"
