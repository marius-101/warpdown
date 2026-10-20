#!/usr/bin/env bash
# Package the extension as dist/warpdown.xpi: for loading in Firefox (also sandboxed/Flatpak Firefox, whose file picker
# only exposes single files) and for uploading to addons.mozilla.org. The Firefox package omits Chrome-only manifest keys.
cd "$(dirname "$0")/.." && python3 -c "
import sys; sys.path.insert(0, '.')
from warpdown.browsers import build_xpi
p = build_xpi()
print('Built', p) if p else sys.exit('extension folder not found')"
