// Retain the production npm dependency closure for image/SBOM scanners even
// though the runtime serves bundles and contains no Node executable.
import { execFileSync } from "node:child_process";
import { readFileSync, mkdirSync, writeFileSync, rmSync } from "node:fs";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";

export function collectPackages(tree, readPackage) {
  const packages = new Map();
  const visited = new WeakSet();
  function visit(node) {
    if (visited.has(node)) return;
    visited.add(node);
    for (const dep of Object.values({
      ...node.dependencies,
      ...node.optionalDependencies,
    })) {
      const pkg = readPackage(dep.path);
      if (typeof pkg.name !== "string" || typeof pkg.version !== "string")
        throw new Error("Incomplete production package metadata");
      const key = `${pkg.name}@${pkg.version}`;
      if (!packages.has(key)) {
        packages.set(key, {
          name: pkg.name,
          version: pkg.version,
          ...(typeof pkg.license === "string" ? { license: pkg.license } : {}),
        });
      }
      visit(dep);
    }
  }
  for (const root of tree) visit(root);
  if (!packages.size)
    throw new Error("Production dependency inventory is empty");
  return packages;
}
if (
  process.argv[1] &&
  import.meta.url === pathToFileURL(resolve(process.argv[1])).href
) {
  const tree = JSON.parse(
    execFileSync("pnpm", ["list", "--prod", "--depth", "Infinity", "--json"], {
      encoding: "utf8",
    }),
  );
  const packages = collectPackages(tree, (path) =>
    JSON.parse(readFileSync(resolve(path, "package.json"), "utf8")),
  );
  rmSync("inventory", { recursive: true, force: true });
  for (const [key, pkg] of packages) {
    const directory = resolve(
      "inventory/node_modules",
      encodeURIComponent(key),
    );
    mkdirSync(directory, { recursive: true });
    writeFileSync(resolve(directory, "package.json"), JSON.stringify(pkg));
  }
}
