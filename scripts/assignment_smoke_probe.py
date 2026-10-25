"""Exercise browser-facing assignment HTTP paths against a disposable Spoolman."""

import json
import os
import threading
import urllib.error
import urllib.request
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
    def do_GET(self):
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


def main():
    base = f"http://{os.environ['HOSTNAME']}:3000"

    def request(
        path, spool=None, status=200, origin=None, data=None, payload=None, method=None
    ):
        body = json.dumps({"spoolId": spool}).encode() if spool is not None else data
        if payload is not None:
            body = json.dumps(payload).encode()
        headers = {"Content-Type": "application/json"}
        if origin:
            headers["Origin"] = origin
        value = urllib.request.Request(
            base + path,
            data=body,
            headers=headers,
            method=method or ("PUT" if body is not None else "GET"),
        )
        try:
            response = urllib.request.urlopen(value, timeout=10)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            text = response.read().decode()
            assert response.status == status, (path, response.status, text)
            return text

    server = ThreadingHTTPServer(("127.0.0.1", 50053), Spoolman)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        assert json.loads(request("/api/v1/printer"))["connected"] is False
        assert json.loads(request("/api/v1/info"))["features"]["trayLocking"] is True
        request("/")
        before = request("/external-spool")
        assert "No spool assigned to this slot" in before, before
        request("/api/v1/trays/255", 43, status=204, origin=base)
        after = request("/external-spool")
        assert "No spool assigned to this slot" not in after, after
        assert "750" in after and "Remove Spool" in after, after
        assert "Remove Spool" in request("/ams/1/tray/1")
        assert json.loads(request("/api/v1/settings"))["trays"]["255"] == 43
        rejection = request("/api/v1/trays/1", 43, status=400)
        assert "External Spool Holder" in rejection
        request("/api/v1/trays/255", -1, status=403, origin="https://other.invalid")
        request("/api/v1/trays/255", status=413, data=b" " * 4097)
        request("/api/v1/trays/255", -1, status=204)
        cleared = request("/external-spool")
        assert "No spool assigned to this slot" in cleared, cleared
        tag = "AABBCCDDEEFF00112233445566778899"
        request("/api/v1/spools/43/rfid", payload={"uuid": tag}, status=204)
        assert SPOOLS[1]["tags"] == [{"uid": tag, "format": "bambu-tray"}]
        request("/api/v1/spools/43/rfid", payload={"uuid": ""}, status=400)
        request(
            "/api/v1/trays/0/rfid/override",
            payload={"uuid": tag},
            method="POST",
            status=400,
        )
        request(
            "/api/v1/trays/0/rfid/override",
            payload={"uuid": tag},
            method="POST",
            status=403,
            origin="https://other.invalid",
        )
        request(
            "/api/v1/trays/0/rfid/unlink",
            payload={"uuid": tag, "spoolId": 42},
            method="POST",
            status=400,
        )
        assert SPOOLS[1]["tags"] == [{"uid": tag, "format": "bambu-tray"}]

    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    unavailable = request("/api/v1/trays/255", 43, status=502)
    assert "Spoolman is unavailable" in unavailable
    assert json.loads(request("/api/v1/settings"))["trays"] == {"0": 42}
    print(
        "Assignment HTTP smoke test passed: pages, cache, assign, clear, "
        "conflicts, outages"
    )


if __name__ == "__main__":
    main()
