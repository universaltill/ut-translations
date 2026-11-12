"""A loopback server that answers from a script, for the retry tests.

Each step is "timeout" (sleep past the client's timeout, then answer 200 late),
an int status, or (status, {headers}). The last step repeats. `hits` records
(method, path) for every request."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SLOW = 0.6  # seconds a "timeout" step holds the reply; tests pass timeout=0.2


def serve(testcase, steps, body=None):
    hits = []
    lock = threading.Lock()
    payload = json.dumps(body if body is not None else []).encode()

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _answer(self):
            n = int(self.headers.get("Content-Length") or 0)
            if n:
                self.rfile.read(n)
            with lock:
                hits.append((self.command, self.path))
                step = steps[min(len(hits), len(steps)) - 1]
            if step == "timeout":
                import time
                time.sleep(SLOW)
                status, headers = 200, {}
            elif isinstance(step, tuple):
                status, headers = step
            else:
                status, headers = step, {}
            try:
                self.send_response(status)
                for k, v in headers.items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            except OSError:
                pass

        do_GET = do_POST = do_PATCH = _answer

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    testcase.addCleanup(srv.server_close)
    testcase.addCleanup(srv.shutdown)
    return f"http://127.0.0.1:{srv.server_address[1]}", hits
