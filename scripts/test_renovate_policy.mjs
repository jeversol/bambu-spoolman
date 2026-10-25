// Test the resolved policy using the exact Renovate engine used by validation.
import assert from 'node:assert/strict';
import { existsSync, readFileSync, realpathSync } from 'node:fs';
import { createRequire } from 'node:module';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

const bin = process.env.PATH.split(path.delimiter)
  .map((directory) => path.join(directory, 'renovate'))
  .find((candidate) => existsSync(candidate));
assert.ok(bin, 'Run through scripts/validate-renovate.sh');
const root = path.dirname(path.dirname(realpathSync(bin)));
const require = createRequire(path.join(root, 'package.json'));
const json5 = require('json5');
const load = (module) => import(pathToFileURL(path.join(root, 'dist', module)));
const { init } = await load('logger/index.js');
await init();
const { getConfig } = await load('config/defaults.js');
const { resolveConfigPresets } = await load('config/presets/index.js');
const { mergeChildConfig } = await load('config/utils.js');
const { applyPackageRules } = await load('util/package-rules/index.js');
const { checkMinimumReleaseAge } = await load('util/minimum-release-age.js');
const { filterVersions } = await load('workers/repository/process/lookup/filter.js');
const { applyVulnerabilityFixFilter } = await load('workers/repository/process/lookup/vulnerability.js');
const { detectVulnerabilityAlerts } = await load('workers/repository/init/vulnerability.js');
const { setPlatformApi } = await load('modules/platform/index.js');
const { default: platforms } = await load('modules/platform/api.js');
const { get: versioning } = await load('modules/versioning/index.js');
const resolved = await resolveConfigPresets(
  json5.parse(readFileSync('.github/renovate.json5', 'utf8')),
);
const config = mergeChildConfig(getConfig(), resolved.config);
const dependency = (overrides = {}) => ({
  manager: 'npm', datasource: 'npm', versioning: 'npm', packageFile: 'frontend/package.json',
  depName: 'example', packageName: 'example', currentVersion: '1.0.0',
  currentValue: '1.0.0', updateType: 'patch', ...overrides,
});
const effective = async (overrides = {}, policy = config) =>
  applyPackageRules(mergeChildConfig(policy, dependency(overrides)));
const recent = new Date(Date.now() - 60 * 60 * 1000).toISOString();
const mature = new Date(Date.now() - 3 * 24 * 60 * 60 * 1000).toISOString();
let cases = 0;
async function scenario(name, test) {
  await test();
  cases += 1;
  console.log(`PASS ${name}`);
}

await scenario('ordinary timestamped releases wait and require timestamps', async () => {
  for (const datasource of ['npm', 'pypi', 'apk', 'github-tags', 'github-releases']) {
    const update = await effective({ datasource });
    assert.equal(update.minimumReleaseAge, '1 day', datasource);
    assert.equal(checkMinimumReleaseAge(update, recent).isPending, true);
    assert.equal(checkMinimumReleaseAge(update, mature).isPending, false);
    assert.equal(checkMinimumReleaseAge(update, undefined).isPending, true);
  }
});
await scenario('runtime, development and pre-1.0 updates require review', async () => {
  for (const currentVersion of ['1.0.0', '0.1.0']) {
    for (const depType of ['dependencies', 'devDependencies']) {
      for (const updateType of ['minor', 'patch', 'major']) {
        assert.equal((await effective({ currentVersion, depType, updateType })).automerge, false);
      }
    }
  }
});
await scenario('only tested validator updates opt into weekly automerge', async () => {
  const validator = { manager: 'pre-commit', datasource: 'github-tags',
    depName: 'renovatebot/pre-commit-hooks', packageName: 'renovatebot/pre-commit-hooks',
    currentVersion: '44.133.0', currentValue: '44.133.0' };
  for (const updateType of ['minor', 'patch']) {
    const update = await effective({ ...validator, updateType });
    assert.equal(update.automerge, true);
    assert.equal(update.minimumReleaseAge, '1 day');
    assert.deepEqual(update.schedule, ['* 0-4 * * 1']);
  }
  assert.equal((await effective({ ...validator, updateType: 'major' })).automerge, false);
});
await scenario('production Python digest rebuilds are the only image automerge', async () => {
  const python = { manager: 'dockerfile', datasource: 'docker', packageFile: 'Dockerfile',
    depName: 'python', packageName: 'python', currentValue: '3.14-alpine3.24' };
  assert.equal((await effective({ ...python, updateType: 'digest' })).automerge, true);
  for (const override of [{ packageFile: '.github/docker/Dockerfile.next-python' },
    { depName: 'ghcr.io/astral-sh/uv', packageName: 'ghcr.io/astral-sh/uv' },
    { updateType: 'minor' }]) {
    assert.equal((await effective({ ...python, updateType: 'digest', ...override })).automerge, false);
  }
});
await scenario('digest and pin updates cannot wait forever on missing timestamps', async () => {
  for (const updateType of ['digest', 'pinDigest', 'pin']) {
    const update = await effective({ manager: 'github-actions', datasource: 'github-tags', updateType });
    assert.equal(update.minimumReleaseAge, null);
    assert.equal(checkMinimumReleaseAge(update, undefined).isPending, false);
    assert.equal(update.automerge, false);
  }
  assert.equal((await effective({ datasource: 'docker' })).minimumReleaseAge, null);
});
await scenario('lockfile maintenance stays weekly, reviewed, and outside age guarantees', async () => {
  const update = mergeChildConfig(await effective({ updateType: 'lockFileMaintenance' }), config.lockFileMaintenance);
  assert.equal(update.enabled, true);
  assert.equal(update.automerge, false);
  assert.equal(update.minimumReleaseAge, null);
  assert.deepEqual(update.schedule, ['before 4am on monday']);
});
await scenario('React runtime/type updates retain a coordinated group', async () => {
  for (const [names, expected] of [[['react', 'react-dom', '@types/react', '@types/react-dom'], 'react packages']]) {
    for (const name of names) assert.equal((await effective({ depName: name, packageName: name })).groupName, expected);
  }
});
await scenario('Node and Python updates stay within supported versions', async () => {
  const node = await effective({ datasource: 'apk', depName: 'nodejs', packageName: 'nodejs' });
  const python = await effective({ manager: 'pep621', datasource: 'python-version', depName: 'python', packageName: 'python' });
  const types = await effective({ depName: '@types/node', packageName: '@types/node' });
  for (const [update, current, versions, expected, scheme] of [
    [node, '24.22.1-r0', ['24.23.0-r0', '25.0.0-r0'], ['24.23.0-r0'], 'apk'],
    [python, '3.14.0', ['3.14.1', '3.15.0'], ['3.14.1'], 'pep440'],
    [types, '24.0.0', ['24.1.0', '25.0.0'], ['24.1.0'], 'npm'],
  ]) {
    assert.deepEqual(filterVersions(update, current, undefined, versions.map((version) => ({ version })), versioning(scheme)).map((r) => r.version), expected);
  }
});
await scenario('prereleases stay in the chosen compatibility minor and out of production', async () => {
  const image = { manager: 'dockerfile', datasource: 'docker', versioning: 'docker',
    depName: 'python', packageName: 'python', currentValue: '3.14-alpine3.24' };
  const production = await effective({ ...image, packageFile: 'Dockerfile' });
  const compatibility = await effective({ ...image, packageFile: '.github/docker/Dockerfile.next-python' });
  const releases = ['3.15.0rc1-alpine', '3.15.0-alpine', '3.16.0rc1-alpine'].map((version) => ({ version }));
  assert.equal(compatibility.ignoreUnstable, false);
  assert.equal(compatibility.automerge, false);
  assert.deepEqual(filterVersions(production, '3.14-alpine3.24', undefined, releases, versioning('docker')), []);
  assert.deepEqual(filterVersions(compatibility, '3.15.0a1-alpine', undefined, releases, versioning('docker')).map((r) => r.version),
    ['3.15.0rc1-alpine', '3.15.0-alpine']);
});
await scenario('real GitHub alerts override grouping, schedules, age and automerge at every severity', async () => {
  for (const severity of ['low', 'moderate', 'high', 'critical']) {
    platforms.set('github', { getVulnerabilityAlerts: async () => [{
      security_vulnerability: { package: { ecosystem: 'npm', name: 'react' },
        first_patched_version: { identifier: '1.0.1' }, severity },
      security_advisory: { identifiers: [], summary: 'Fixture', description: 'Fixture', severity },
    }] });
    setPlatformApi('github');
    const policy = await detectVulnerabilityAlerts(config);
    const update = await effective({ depName: 'react', packageName: 'react' }, policy);
    assert.equal(update.isVulnerabilityAlert, true);
    assert.equal(update.vulnerabilityFixVersion, '1.0.1');
    assert.equal(update.minimumReleaseAge, null);
    assert.equal(checkMinimumReleaseAge(update, recent).isPending, false);
    assert.equal(update.automerge, false);
    assert.equal(update.groupName, null);
    assert.deepEqual(update.schedule, []);
    assert.deepEqual(update.labels, ['security']);
    assert.equal(update.vulnerabilityFixStrategy, 'lowest');
    const selected = applyVulnerabilityFixFilter(update, {}, versioning('npm'),
      ['1.0.0', '1.0.1', '1.1.0'].map((version) => ({ version })));
    assert.deepEqual(selected.releases.map((release) => release.version), ['1.0.1']);
    assert.equal((await applyPackageRules(update)).minimumReleaseAge, null);
  }
});
await scenario('a security alert disables even an explicitly eligible automerge group', async () => {
  platforms.set('github', { getVulnerabilityAlerts: async () => [{
    security_vulnerability: { package: { ecosystem: 'actions', name: 'renovatebot/pre-commit-hooks' },
      first_patched_version: { identifier: '44.134.0' }, severity: 'high' },
    security_advisory: { identifiers: [], summary: 'Fixture', description: 'Fixture', severity: 'high' },
  }] });
  setPlatformApi('github');
  const policy = await detectVulnerabilityAlerts(config);
  const update = await effective({ manager: 'pre-commit', datasource: 'github-tags',
    depName: 'renovatebot/pre-commit-hooks', packageName: 'renovatebot/pre-commit-hooks',
    currentVersion: '44.133.0', currentValue: '44.133.0' }, policy);
  assert.equal(update.isVulnerabilityAlert, true);
  assert.equal(update.automerge, false);
  assert.equal(update.minimumReleaseAge, null);
  assert.deepEqual(update.schedule, []);
});
await scenario('fresh branch checks and test requirements are enabled', async () => {
  assert.equal(config.rebaseWhen, 'behind-base-branch');
  assert.equal(config.ignoreTests, false);
  assert.equal(config.platformAutomerge, false);
  assert.equal(config.internalChecksFilter, 'strict');
});
await scenario('pnpm cooldown stays disabled and Python compatibility has one image owner', async () => {
  assert.match(readFileSync('frontend/pnpm-workspace.yaml', 'utf8'), /^minimumReleaseAge: 0$/m);
  assert.doesNotMatch(readFileSync('.github/workflows/next-python.yaml', 'utf8'), /NEXT_PYTHON_IMAGE=/);
  assert.match(readFileSync('.github/docker/Dockerfile.next-python', 'utf8'), /^ARG NEXT_PYTHON_IMAGE=python:/m);
});
console.log(`${cases} effective Renovate policy scenarios passed`);
