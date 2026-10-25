import assert from "node:assert/strict";
import { chromium } from "playwright";
const base = "http://127.0.0.1:3000";
const browser = await chromium.launch();
const page = await browser.newPage();
const errors = [];
page.on("pageerror", (error) => errors.push(error.message));
page.setDefaultTimeout(15_000);
async function visible(locator) { await locator.waitFor({ state: "visible" }); }
async function assignment(id, spoolId) {
  const response = await page.request.put(`${base}/api/v1/trays/${id}`, { data: { spoolId } });
  assert.equal(response.status(), 204, await response.text());
}
async function failure(enabled) {
  const response = await page.request.post("http://127.0.0.1:50053/__test/failure", { data: { enabled } });
  assert.equal(response.status(), 204);
}
try {
  await page.goto(`${base}/external-spool`);
  await visible(page.getByText("No spool assigned to this slot", { exact: true }));
  await page.getByPlaceholder("Search ID, name, vendor, material, or color").fill("43");
  await page.locator("#spool-43").click();
  await page.getByRole("button", { name: "Update", exact: true }).click();
  await visible(page.getByRole("button", { name: "Remove Spool" }));
  let settings = await (await page.request.get(`${base}/api/v1/settings`)).json();
  assert.equal(settings.trays["255"], 43);
  await page.reload();
  await visible(page.getByText(/^750g remaining \(/));
  await page.getByRole("button", { name: "Remove Spool" }).click();
  await visible(page.getByText("No spool assigned to this slot", { exact: true }));
  await page.getByRole("button", { name: "Toggle light and dark theme" }).click();
  const dark = await page.locator("html").evaluate((node) => node.classList.contains("dark"));
  await page.reload();
  assert.equal(await page.locator("html").evaluate((node) => node.classList.contains("dark")), dark);
  await page.goto(`${base}/ams/1/tray/1`);
  await visible(page.getByRole("button", { name: "Remove Spool" }));
  await page.getByRole("link", { name: "Back", exact: true }).click();
  await visible(page.getByRole("heading", { name: "Printer mappings" }));
  await page.goBack();
  await visible(page.getByRole("button", { name: "Remove Spool" }));
  await page.goForward();
  const mapped = page.locator("article").first();
  await visible(mapped.getByText("#42 · Smoke spool 42", { exact: true }));
  // Polling pauses while editing, then resumes without losing the dialog state.
  await mapped.getByRole("button", { name: "Change mapping" }).click();
  await visible(page.getByRole("dialog"));
  await assignment(0, 43);
  await page.waitForTimeout(5500);
  assert.equal(await mapped.getByText("#42 · Smoke spool 42", { exact: true }).count(), 1);
  await page.keyboard.press("Escape");
  await visible(mapped.getByText("#43 · Smoke spool 43", { exact: true }));
  await assignment(0, 42);
  await visible(mapped.getByText("#42 · Smoke spool 42", { exact: true }));
  // Outages retain the last good data and provide an explicit retry.
  await failure(true);
  await visible(page.getByRole("alert").filter({ hasText: "Could not refresh" }));
  assert.equal(await mapped.getByText("#42 · Smoke spool 42", { exact: true }).count(), 1);
  await failure(false);
  await page.getByRole("button", { name: "Try again" }).click();
  await page.getByRole("alert").filter({ hasText: "Could not refresh" }).waitFor({ state: "hidden" });
  await failure(true);
  await page.reload();
  await visible(page.getByRole("alert").filter({ hasText: "Could not refresh" }));
  await failure(false);
  await page.getByRole("button", { name: "Try again" }).click();
  await visible(mapped.getByText("#42 · Smoke spool 42", { exact: true }));
  settings = await (await page.request.get(`${base}/api/v1/settings`)).json();
  assert.deepEqual(settings.trays, { "0": 42 });
  assert.deepEqual(errors, []);
  console.log("Browser smoke passed: assignment/removal, deep links, history, theme, paused polling, outage/recovery");
} finally {
  await failure(false);
  await browser.close();
}
