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

for exe, arg in (("yt-dlp.exe", "--version"), ("ffmpeg.exe", "-version")):
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

# The browser hand-off when Warpdown is NOT running: the host starts it, delivers the download, then ends. Warpdown must
# keep running after the host is gone (Firefox ends everything a host started unless it was started independently).
import time

# Normal data folder (Explorer-started programs do not inherit this script's private environment).
env2 = {k: v for k, v in os.environ.items()}
msg = json.dumps({"action": "add", "url": "http://127.0.0.1:9/test.bin"}).encode()
r = subprocess.run([str(root / "warpdown-host.exe")], input=struct.pack("=I", len(msg)) + msg, env=env2,
                   capture_output=True, timeout=120)
n = struct.unpack("=I", r.stdout[:4])[0]
reply = json.loads(r.stdout[4:4 + n])
log = Path(os.environ["LOCALAPPDATA"]) / "warpdown" / "host.log"
print("host.log:\n" + (log.read_text()[-800:] if log.exists() else "(none)"))
print("host reply (Warpdown was not running):", reply)
time.sleep(5)
alive = "warpdown.exe" in subprocess.run(["tasklist"], capture_output=True, text=True).stdout.lower()
subprocess.run(["taskkill", "/F", "/IM", "warpdown.exe"], capture_output=True)
assert reply == {"ok": True}, reply
assert alive, "Warpdown ended together with the host"
print("host hand-off smoke test passed")
