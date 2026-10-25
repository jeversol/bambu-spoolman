// Share the pre-commit hook's version rather than maintaining another tool pin.
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';

const hooks = readFileSync('.pre-commit-config.yaml', 'utf8');
const versions = [...hooks.matchAll(
  /^\s*- repo: https:\/\/github\.com\/renovatebot\/pre-commit-hooks\s*\n\s+rev: (\d+\.\d+\.\d+)\s*$/gm,
)];
assert.equal(versions.length, 1, 'Expected exactly one pinned Renovate hook');
const version = versions[0][1];
const prefix = ['exec', '--yes', '--package', `renovate@${version}`, '--'];
const run = (args) => execFileSync('npm', [...prefix, ...args], { stdio: 'inherit' });
run(['renovate-config-validator', '--strict', '.github/renovate.json5']);
run(['node', 'scripts/test_renovate_policy.mjs']);
