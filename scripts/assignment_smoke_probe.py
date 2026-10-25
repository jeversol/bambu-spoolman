"""Exercise browser-facing assignment HTTP paths against a disposable Spoolman."""

import json
import os
import urllib.error
import urllib.request


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

    assert json.loads(request("/api/v1/printer"))["connected"] is False
    assert json.loads(request("/api/v1/info"))["features"]["trayLocking"] is True
    request("/")
    before = request("/external-spool")
    assert 'id="root"' in before, before
    request("/api/v1/trays/255", 43, status=204, origin=base)
    after = request("/external-spool")
    assert 'id="root"' in after, after

    assert 'id="root"' in request("/ams/1/tray/1")
    assert json.loads(request("/api/v1/settings"))["trays"]["255"] == 43
    rejection = request("/api/v1/trays/1", 43, status=400)
    assert "External Spool Holder" in rejection
    request("/api/v1/trays/255", -1, status=403, origin="https://other.invalid")
    request("/api/v1/trays/255", status=413, data=b" " * 4097)
    request("/api/v1/trays/255", -1, status=204)
    cleared = request("/external-spool")
    assert 'id="root"' in cleared, cleared
    tag = "AABBCCDDEEFF00112233445566778899"
    request("/api/v1/spools/43/rfid", payload={"uuid": tag}, status=204)
    assert json.loads(request("/api/v1/spools/43"))["tags"] == [
        {"uid": tag, "format": "bambu-tray"}
    ]
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
    assert json.loads(request("/api/v1/spools/43"))["tags"] == [
        {"uid": tag, "format": "bambu-tray"}
    ]

    urllib.request.urlopen(
        urllib.request.Request(
            "http://127.0.0.1:50053/__test/failure",
            data=b'{"enabled":true}',
            headers={"Content-Type": "application/json"},
        )
    ).close()
    unavailable = request("/api/v1/trays/255", 43, status=502)
    assert "Spoolman is unavailable" in unavailable
    assert json.loads(request("/api/v1/settings"))["trays"] == {"0": 42}
    urllib.request.urlopen(
        urllib.request.Request(
            "http://127.0.0.1:50053/__test/failure",
            data=b'{"enabled":false}',
            headers={"Content-Type": "application/json"},
        )
    ).close()
    print(
        "Assignment HTTP smoke test passed: pages, cache, assign, clear, "
        "conflicts, outages"
    )


if __name__ == "__main__":
    main()
