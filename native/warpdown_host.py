#!/usr/bin/env python3
"""Native messaging host: browser extension -> running Warpdown instance."""
import json
import os
import struct
import subprocess
import sys
import time

from warpdown import ipc, osutil
from warpdown.util import data_dir


def read_message():
    raw = sys.stdin.buffer.read(4)
    if len(raw) < 4:
        return None
    n = struct.unpack("=I", raw)[0]
    return json.loads(sys.stdin.buffer.read(n))


def write_message(obj):
    data = json.dumps(obj).encode()
    sys.stdout.buffer.write(struct.pack("=I", len(data)) + data)
    sys.stdout.buffer.flush()


def log(text):
    with open(os.path.join(str(data_dir()), "host.log"), "a") as f:
        f.write(f"{time.strftime('%H:%M:%S')} {text}\n")


def main():
    log("host started")
    while True:
        msg = read_message()
        if msg is None:
            return
        log("got " + json.dumps(msg)[:200])
        if msg.get("action") == "ping":
            write_message({"ok": True})
            continue
        ok = ipc.send(msg)
        if not ok:
            app = osutil.helper("warpdown")                      # the installed Windows program, or Python from source
            subprocess.Popen([app] if app else [sys.executable, "-m", "warpdown"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **osutil.detached())
            for _ in range(30):
                time.sleep(0.3)
                if ipc.send(msg):
                    ok = True
                    break
        write_message({"ok": ok})


if __name__ == "__main__":
    main()
