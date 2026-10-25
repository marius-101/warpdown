"""Smoke test of the built Linux program: does it start, quit, talk to the browser host, survive a cold-start hand-off?"""
import json
import os
import signal
import struct
import subprocess
import sys
import tempfile
import time
from pathlib import Path

root = Path(sys.argv[1]).resolve()
tmp = tempfile.mkdtemp(prefix="wdsmoke")
run = Path(tmp) / "run"
run.mkdir(mode=0o700)
env = dict(os.environ, QT_QPA_PLATFORM="offscreen", XDG_DATA_HOME=tmp + "/data", XDG_CONFIG_HOME=tmp + "/conf",
           XDG_RUNTIME_DIR=str(run), HOME=tmp)

for exe, arg in (("yt-dlp", "--version"), ("aria2c", "--version"), ("ffmpeg", "-version")):
    p = root / exe
    if p.exists():
        r = subprocess.run([str(p), arg], capture_output=True, text=True, timeout=60)
        assert r.returncode == 0, (exe, r.stderr)
        print(exe, "ok:", r.stdout.splitlines()[0][:70])

r = subprocess.run([str(root / "warpdown")], env=dict(env, WARPDOWN_QUIT_AFTER_MS="4000"), timeout=90,
                   capture_output=True, text=True)
print("warpdown exit code", r.returncode, r.stderr[-300:])
assert r.returncode == 0


def host(msg: dict) -> dict:
    data = json.dumps(msg).encode()
    out = subprocess.run([str(root / "warpdown-host")], input=struct.pack("=I", len(data)) + data, env=env,
                         capture_output=True, timeout=120).stdout
    return json.loads(out[4:4 + struct.unpack("=I", out[:4])[0]])


assert host({"action": "ping"}) == {"ok": True}
print("host ping ok")

reply = host({"action": "add", "url": "http://127.0.0.1:9/test.bin"})         # Warpdown is not running: the host starts it
print("hand-off reply:", reply)
time.sleep(4)
alive = subprocess.run(["pgrep", "-f", str(root / "warpdown") + "$"], capture_output=True, text=True).stdout.split()
log = Path(tmp) / "data" / "warpdown" / "host.log"
print(log.read_text()[-400:] if log.exists() else "no host.log")
for pid in alive:
    os.kill(int(pid), signal.SIGTERM)
assert reply == {"ok": True}, reply
assert alive, "Warpdown ended together with the host"
print("smoke test passed")
