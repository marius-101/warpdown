#!/usr/bin/env bash
# Package the extension as dist/warpdown-chrome.zip for Chrome, Brave, Edge, Vivaldi and Chromium: unzip it and choose
# the folder with "Load unpacked". The Chrome package omits Firefox-only manifest keys.
cd "$(dirname "$0")/.." && python3 -c "
import sys; sys.path.insert(0, '.')
from warpdown.browsers import build_chrome_zip
p = build_chrome_zip()
print('Built', p) if p else sys.exit('extension folder not found')"
