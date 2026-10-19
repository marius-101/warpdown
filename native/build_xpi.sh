#!/usr/bin/env bash
# Package the extension as dist/kudm.xpi (needed for sandboxed/Flatpak Firefox, whose file picker only exposes single files).
cd "$(dirname "$0")/.." && mkdir -p dist && rm -f dist/kudm.xpi && python3 -c "
import zipfile, pathlib
with zipfile.ZipFile('dist/kudm.xpi','w',zipfile.ZIP_DEFLATED) as z:
    [z.write(f, f.name) for f in sorted(pathlib.Path('extension').iterdir())]"
echo "Built dist/kudm.xpi"
