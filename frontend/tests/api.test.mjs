import assert from "node:assert/strict";
import { afterEach, mock, test } from "node:test";
import { ApiError, apiRequest } from "../lib/api.ts";

afterEach(() => mock.restoreAll());

test("assignment uses JSON and never retries a mutation", async () => {
  const fetch = mock.method(
    globalThis,
    "fetch",
    async () => new Response(null, { status: 204 }),
  );
  await apiRequest("/trays/255", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ spoolId: 42 }),
  });
  assert.equal(fetch.mock.callCount(), 1);
  const [url, options] = fetch.mock.calls[0].arguments;
  assert.equal(url, "/api/v1/trays/255");
  assert.equal(options.cache, "no-store");
  assert.equal(options.redirect, "error");
  assert.equal(options.method, "PUT");
  assert.deepEqual(JSON.parse(options.body), { spoolId: 42 });
});

test("polling returns fresh settings from the chosen backend", async () => {
  const fetch = mock.method(globalThis, "fetch", async () =>
    Response.json({ trays: { 0: 42 } }),
  );
  assert.deepEqual(
    await apiRequest("/settings", {}, "http://127.0.0.1:50052"),
    { trays: { 0: 42 } },
  );
  assert.equal(
    fetch.mock.calls[0].arguments[0],
    "http://127.0.0.1:50052/api/v1/settings",
  );
});

test("backend rejection preserves the actionable message and status", async () => {
  mock.method(globalThis, "fetch", async () =>
    Response.json({ error: "Tray is locked" }, { status: 400 }),
  );
  await assert.rejects(
    apiRequest("/trays/0", { method: "PUT" }),
    (error) =>
      error instanceof ApiError &&
      error.status === 400 &&
      error.message === "Tray is locked",
  );
});

test("a proxy HTML error remains a readable failure", async () => {
  mock.method(
    globalThis,
    "fetch",
    async () => new Response("<html>unavailable</html>", { status: 502 }),
  );
  await assert.rejects(apiRequest("/settings"), {
    message: "Request failed (502)",
  });
});

test("network failure is surfaced without an automatic retry", async () => {
  const fetch = mock.method(globalThis, "fetch", async () => {
    throw new Error("Network unavailable");
  });
  await assert.rejects(apiRequest("/trays/0", { method: "PUT" }), {
    message: "Network unavailable",
  });
  assert.equal(fetch.mock.callCount(), 1);
});

test("successful and partially failed writes invalidate browser reads", async () => {
  const events = new EventTarget();
  globalThis.window = events;
  let changes = 0;
  events.addEventListener("bambu-spoolman:changed", () => {
    changes += 1;
  });
  try {
    const fetch = mock.method(
      globalThis,
      "fetch",
      async () => new Response(null, { status: 204 }),
    );
    await apiRequest("/trays/0", { method: "PUT" });
    assert.equal(changes, 1);
    fetch.mock.mockImplementation(async () =>
      Response.json({ error: "partially applied" }, { status: 502 }),
    );
    await assert.rejects(
      apiRequest("/trays/0/rfid/unlink", { method: "POST" }),
      /partially applied/,
    );
    assert.equal(changes, 2);
    fetch.mock.mockImplementation(async () => {
      throw new Error("network failure");
    });
    await assert.rejects(
      apiRequest("/trays/0", { method: "PUT" }),
      /network failure/,
    );
    assert.equal(changes, 3);
    fetch.mock.mockImplementation(async () => Response.json({ trays: {} }));
    await apiRequest("/settings");
    assert.equal(changes, 3);
  } finally {
    delete globalThis.window;
  }
});
