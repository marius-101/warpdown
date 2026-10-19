#!/usr/bin/env python3
"""Native messaging host: browser extension -> running KUDM instance."""
import json
import struct
import subprocess
import sys
import time

from PySide6.QtCore import QCoreApplication

from kudm import ipc


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


def main():
    app = QCoreApplication(sys.argv)  # noqa: F841 (needed by QLocalSocket)
    while True:
        msg = read_message()
        if msg is None:
            return
        if msg.get("action") == "ping":
            write_message({"ok": True})
            continue
        ok = ipc.send(msg)
        if not ok:
            subprocess.Popen([sys.executable, "-m", "kudm"], start_new_session=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            for _ in range(30):
                time.sleep(0.3)
                if ipc.send(msg):
                    ok = True
                    break
        write_message({"ok": ok})


if __name__ == "__main__":
    main()
