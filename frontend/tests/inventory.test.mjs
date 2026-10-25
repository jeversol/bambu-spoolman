import assert from "node:assert/strict";
import { test } from "node:test";
import { collectPackages } from "../scripts/write-asset-inventory.mjs";
test("scanner inventory retains production and optional dependencies, deduplicates and excludes development tools", () => {
  const leaf = { path: "leaf" };
  const tree = [
    {
      dependencies: { app: { path: "app", dependencies: { leaf } } },
      optionalDependencies: { leaf },
      devDependencies: { compiler: { path: "compiler" } },
    },
  ];
  const result = collectPackages(tree, (path) => ({
    name: `@scope/${path}`,
    version: "1.2.3",
    license: "MIT",
    secret: "excluded",
  }));
  assert.deepEqual([...result.keys()].sort(), [
    "@scope/app@1.2.3",
    "@scope/leaf@1.2.3",
  ]);
  assert.deepEqual(result.get("@scope/leaf@1.2.3"), {
    name: "@scope/leaf",
    version: "1.2.3",
    license: "MIT",
  });
});
test("incomplete or empty inventory fails the build", () => {
  assert.throws(() => collectPackages([], () => ({})), /empty/);
  assert.throws(
    () =>
      collectPackages([{ dependencies: { bad: { path: "bad" } } }], () => ({})),
    /Incomplete/,
  );
});

test("peer variants of the same package retain both dependency branches", () => {
  const result = collectPackages(
    [
      {
        dependencies: {
          first: { path: "shared", dependencies: { a: { path: "a" } } },
          second: { path: "shared", dependencies: { b: { path: "b" } } },
        },
      },
    ],
    (name) => ({ name, version: "1.0.0" }),
  );
  assert.deepEqual([...result.keys()].sort(), [
    "a@1.0.0",
    "b@1.0.0",
    "shared@1.0.0",
  ]);
});
