"""Disposable upstream fixture; never installed as an application route."""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SPOOLS = [
    {
        "id": spool_id,
        "remaining_weight": 750,
        "remaining_length": 100,
        "filament": {"name": f"Smoke spool {spool_id}", "material": "PLA"},
    }
    for spool_id in (42, 43)
]


class Spoolman(BaseHTTPRequestHandler):
    failure = False

    def do_GET(self):
        if self.failure and self.path.startswith("/api/v1/spool"):
            self.send_error(503)
            return
        if self.path == "/api/v1/info":
            value = {"version": "0.27.0"}
        elif self.path == "/api/v1/health":
            value = {"status": "healthy"}
        elif self.path == "/api/v1/spool":
            value = SPOOLS
        elif self.path in ("/api/v1/spool/42", "/api/v1/spool/43"):
            value = SPOOLS[int(self.path.rsplit("/", 1)[1]) - 42]
        else:
            self.send_error(404)
            return
        body = json.dumps(value).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path == "/__test/failure":
            Spoolman.failure = json.loads(
                self.rfile.read(int(self.headers["Content-Length"]))
            ).get("enabled", False)
            self.send_response(204)
            self.end_headers()
            return
        if self.path != "/api/v1/spool/43/tag":
            self.send_error(404)
            return
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        SPOOLS[1]["tags"] = [body]
        self.send_response(201)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", 50053), Spoolman).serve_forever()
