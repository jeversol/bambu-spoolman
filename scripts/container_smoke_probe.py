"""Exercise the installed application inside its restricted production container."""

import json
import os
import sys
import tempfile
import urllib.request

import grpc
from google.protobuf.empty_pb2 import Empty

from bambu_spoolman.grpc.bambu_spoolman_pb2_grpc import BambuSpoolmanStub


def probe(expected_spool):
    with urllib.request.urlopen(
        f"http://{os.environ['HOSTNAME']}:3000/manifest.webmanifest", timeout=2
    ) as response:
        assert response.status == 200
    with grpc.insecure_channel("127.0.0.1:50051") as channel:
        settings = BambuSpoolmanStub(channel).GetSettings(Empty(), timeout=2)
        assert settings.trays["0"] == expected_spool, settings
    assert os.stat("/proc/1").st_uid == 10001
    assert os.statvfs("/app").f_flag & os.ST_RDONLY
    with open("/proc/1/status") as status_file:
        status = dict(line.split(":", 1) for line in status_file if ":" in line)
    assert int(status["CapEff"].strip(), 16) == 0
    assert status["NoNewPrivs"].strip() == "1"
    for directory in ("/config", "/tmp", "/app/frontend/.next/cache"):
        with tempfile.TemporaryFile(dir=directory) as writable:
            writable.write(b"smoke test")
    for path in ("/config/settings.json", "/config/checkpoint/smoke-test.json"):
        assert os.stat(path).st_uid == 10001
    with open("/config/checkpoint/smoke-test.json") as checkpoint:
        assert json.load(checkpoint) == {"smoke_test": "persisted"}


if __name__ == "__main__":
    probe(int(sys.argv[1]))
