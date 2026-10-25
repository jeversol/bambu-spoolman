"""Exercise the installed application inside its restricted production container."""

import json
import os
import shutil
import socket
import sys
import tempfile
import urllib.request
from pathlib import Path


def probe(expected_spool):
    with urllib.request.urlopen(
        f"http://{os.environ['HOSTNAME']}:3000/manifest.webmanifest", timeout=2
    ) as response:
        assert response.status == 200
    assert shutil.which("node") is None
    assert "python" in Path("/proc/1/exe").resolve().name
    for port in (50051, 50052):
        with socket.socket() as probe_socket:
            probe_socket.settimeout(0.2)
            assert probe_socket.connect_ex(("127.0.0.1", port)) != 0
    with urllib.request.urlopen(
        f"http://{os.environ['HOSTNAME']}:3000/api/v1/settings", timeout=2
    ) as response:
        assert json.load(response)["trays"]["0"] == expected_spool
        assert response.headers["Cache-Control"] == "no-store"
    assert os.stat("/proc/1").st_uid == 10001
    assert os.statvfs("/app").f_flag & os.ST_RDONLY
    with open("/proc/1/status") as status_file:
        status = dict(line.split(":", 1) for line in status_file if ":" in line)
    assert int(status["CapEff"].strip(), 16) == 0
    assert status["NoNewPrivs"].strip() == "1"
    for directory in ("/config", "/tmp"):
        with tempfile.TemporaryFile(dir=directory) as writable:
            writable.write(b"smoke test")
    for path in ("/config/settings.json", "/config/checkpoint/smoke-test.json"):
        assert os.stat(path).st_uid == 10001
    with open("/config/checkpoint/smoke-test.json") as checkpoint:
        assert json.load(checkpoint) == {"smoke_test": "persisted"}


if __name__ == "__main__":
    probe(int(sys.argv[1]))
