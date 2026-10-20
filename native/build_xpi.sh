#!/usr/bin/env bash
# Package the extension as dist/warpdown.xpi (needed for sandboxed/Flatpak Firefox, whose file picker only exposes single files).
cd "$(dirname "$0")/.." && mkdir -p dist && rm -f dist/warpdown.xpi && python3 -c "
import zipfile, pathlib
with zipfile.ZipFile('dist/warpdown.xpi','w',zipfile.ZIP_DEFLATED) as z:
    for f in sorted(pathlib.Path('extension').rglob('*')):
        if f.is_file(): z.write(f, f.relative_to('extension').as_posix())"
echo "Built dist/warpdown.xpi"
