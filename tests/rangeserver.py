"""Tiny HTTP server with Range support for tests."""
import http.server
import re
import threading


def make_server(payload: bytes, ranges: bool = True, delay: float = 0.0):
    class H(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def do_GET(self):
            data = payload
            m = re.match(r"bytes=(\d+)-(\d*)", self.headers.get("Range", ""))
            if m and ranges:
                s = int(m.group(1))
                e = int(m.group(2)) if m.group(2) else len(data) - 1
                e = min(e, len(data) - 1)
                body = data[s:e + 1]
                self.send_response(206)
                self.send_header("Content-Range", f"bytes {s}-{e}/{len(data)}")
            else:
                body = data
                self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Content-Disposition", 'attachment; filename="test file.bin"')
            self.end_headers()
            try:
                for i in range(0, len(body), 65536):
                    self.wfile.write(body[i:i + 65536])
                    if delay:
                        import time; time.sleep(delay)
            except (BrokenPipeError, ConnectionResetError):
                pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}/f"
