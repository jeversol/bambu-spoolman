import assert from "node:assert/strict";
import { test } from "node:test";
import { parseSpool } from "../lib/spool-model.ts";
import { getRemainingPercent, toDashboardSpool } from "../lib/dashboard.ts";

test("HTTP inventory retains dashboard values and multicolor presentation", () => {
  const spool = parseSpool({
    id: 42,
    initial_weight: 1000,
    remaining_weight: 750,
    remaining_length: 150,
    used_length: 50,
    filament: {
      name: "Ocean",
      material: "PLA",
      color_hex: "123456",
      multi_color_direction: "coaxial",
      multi_color_hexes: "123456,abcdef",
      vendor: { name: "Example" },
    },
  });
  assert.deepEqual(toDashboardSpool(spool), {
    id: 42,
    name: "Ocean",
    vendor: "Example",
    material: "PLA",
    colorHex: "#123456",
    remainingWeight: 750,
    remainingPercent: 75,
  });
  assert.equal(spool.filament.multiColorDirection, "coaxial");
  assert.equal(spool.filament.multiColorHexes, "123456,abcdef");
});

test("nullable and absent fields retain the UI's empty defaults", () => {
  const spool = parseSpool({ id: 1, initial_weight: null, filament: null });
  assert.equal(spool.initialWeight, 0);
  assert.equal(spool.remainingLength, 0);
  assert.equal(spool.usedLength, 0);
  assert.equal(spool.filament, undefined);
  assert.equal(getRemainingPercent(spool), null);
  assert.deepEqual(toDashboardSpool(spool), {
    id: 1,
    name: "Spool #1",
    vendor: null,
    material: null,
    colorHex: null,
    remainingWeight: 0,
    remainingPercent: null,
  });
  const filament = parseSpool({ id: 1, filament: { vendor: null } }).filament;
  assert.equal(filament.name, "");
  assert.equal(filament.colorHex, "");
  assert.equal(filament.vendor, undefined);
});

test("weight fallback, length priority, and percentage bounds are preserved", () => {
  const spool = parseSpool({
    id: 1,
    initial_weight: 1000,
    remaining_weight: 250,
  });
  assert.equal(getRemainingPercent(spool), 25);
  assert.equal(getRemainingPercent({ ...spool, remainingWeight: 1200 }), 100);
  assert.equal(getRemainingPercent({ ...spool, remainingWeight: -1 }), 0);
  assert.equal(
    getRemainingPercent({ ...spool, remainingLength: 75, usedLength: 25 }),
    75,
  );
});

test("custom extra fields retain their spelling and nested values at every level", () => {
  const extra = {
    rfid_tag: "TAG",
    custom_field: { inner_key: [1, null, true] },
  };
  const spool = parseSpool({
    id: 42,
    extra,
    filament: { extra, vendor: { extra } },
    future_api_field: "ignored",
  });
  assert.deepEqual(spool.extra, extra);
  assert.deepEqual(spool.filament.extra, extra);
  assert.deepEqual(spool.filament.vendor.extra, extra);
});

test("malformed IDs cannot become valid assignments through coercion", () => {
  for (const id of [
    null,
    undefined,
    "42",
    0,
    -1,
    1.5,
    NaN,
    Infinity,
    2 ** 53,
  ]) {
    assert.throws(() => parseSpool({ id }), /spool ID/);
  }
  for (const value of [null, [], "42"]) {
    assert.throws(() => parseSpool(value), /Spoolman object/);
  }
});

test("malformed upstream values remain errors instead of misleading UI data", () => {
  for (const field of [
    "initial_weight",
    "remaining_weight",
    "remaining_length",
    "used_length",
  ]) {
    for (const value of ["750", false, NaN, Infinity, {}]) {
      assert.throws(
        () => parseSpool({ id: 42, [field]: value }),
        /weight or length/,
      );
    }
  }
  assert.throws(() => parseSpool({ id: 42, filament: [] }), /object/);
  assert.throws(() => parseSpool({ id: 42, filament: { name: 42 } }), /text/);
  assert.throws(() => parseSpool({ id: 42, extra: [] }), /object/);
});
