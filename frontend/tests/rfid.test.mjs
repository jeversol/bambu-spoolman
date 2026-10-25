import assert from "node:assert/strict";
import { afterEach, mock, test } from "node:test";
import {
  linkRfidMapping,
  overrideRfidMapping,
  unlinkRfidMapping,
} from "../lib/client/rfid.ts";

afterEach(() => mock.restoreAll());

test("RFID linking sends the spool and tag through HTTP", async () => {
  const fetch = mock.method(
    globalThis,
    "fetch",
    async () => new Response(null, { status: 204 }),
  );
  assert.deepEqual(await linkRfidMapping(42, "TAG"), { error: null });
  const [path, request] = fetch.mock.calls[0].arguments;
  assert.equal(path, "/api/v1/spools/42/rfid");
  assert.equal(request.method, "PUT");
  assert.deepEqual(JSON.parse(request.body), { uuid: "TAG" });
});

test("permanent unlink is one server-coordinated request", async () => {
  const fetch = mock.method(
    globalThis,
    "fetch",
    async () => new Response(null, { status: 204 }),
  );
  assert.deepEqual(await unlinkRfidMapping(0, 42, "TAG"), { error: null });
  assert.equal(fetch.mock.callCount(), 1);
  const [path, request] = fetch.mock.calls[0].arguments;
  assert.equal(path, "/api/v1/trays/0/rfid/unlink");
  assert.equal(request.method, "POST");
  assert.deepEqual(JSON.parse(request.body), { spoolId: 42, uuid: "TAG" });
});

test("override preserves a stale-tag rejection for the dialog", async () => {
  const fetch = mock.method(globalThis, "fetch", async () =>
    Response.json(
      { error: "The RFID tag is no longer present in this tray" },
      { status: 400 },
    ),
  );
  const result = await overrideRfidMapping(0, "OLD");
  assert.match(result.error, /no longer present/);
  assert.equal(
    fetch.mock.calls[0].arguments[0],
    "/api/v1/trays/0/rfid/override",
  );
});

test("partial unlink failure is displayed and is never retried automatically", async () => {
  const message =
    "Automatic mapping is paused, but permanent unlinking could not be confirmed";
  const fetch = mock.method(globalThis, "fetch", async () =>
    Response.json({ error: message }, { status: 502 }),
  );
  assert.deepEqual(await unlinkRfidMapping(0, 42, "TAG"), { error: message });
  assert.equal(fetch.mock.callCount(), 1);
});
