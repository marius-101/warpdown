"""Smoke test of the built Windows program (run by CI after build.ps1): does it start, quit, talk to the browser host?"""
import json
import os
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

root = Path(sys.argv[1])
env = dict(os.environ, QT_QPA_PLATFORM="offscreen", WARPDOWN_QUIT_AFTER_MS="4000",
           LOCALAPPDATA=tempfile.mkdtemp(), APPDATA=tempfile.mkdtemp())

for exe, arg in (("yt-dlp.exe", "--version"), ("aria2c.exe", "--version"), ("ffmpeg.exe", "-version")):
    r = subprocess.run([str(root / exe), arg], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, (exe, r.stderr)
    print(exe, "ok:", r.stdout.splitlines()[0][:70])

r = subprocess.run([str(root / "warpdown.exe")], env=env, timeout=90, capture_output=True, text=True)
print("warpdown.exe exit code", r.returncode, r.stderr[-300:])
assert r.returncode == 0

msg = json.dumps({"action": "ping"}).encode()
r = subprocess.run([str(root / "warpdown-host.exe")], input=struct.pack("=I", len(msg)) + msg, env=env,
                   capture_output=True, timeout=60)
n = struct.unpack("=I", r.stdout[:4])[0]
reply = json.loads(r.stdout[4:4 + n])
print("host reply", reply)
assert reply == {"ok": True}
print("smoke test passed")
