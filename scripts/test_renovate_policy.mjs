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
const { branchifyUpgrades } = await load('workers/repository/updates/branchify.js');
const { cronMatches } = await load('workers/repository/update/branch/schedule.js');
const { DateTime } = require('luxon');
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
const threeDaysOld = new Date(Date.now() - 3 * 24 * 60 * 60 * 1000).toISOString();
const mature = new Date(Date.now() - 8 * 24 * 60 * 60 * 1000).toISOString();
const weeklySchedule = ['* 0-5 * * 1'];
const routineDependencies = [
  dependency({ depName: 'react', packageName: 'react' }),
  dependency({ depName: 'react-dom', packageName: 'react-dom', updateType: 'minor' }),
  dependency({ depName: '@types/react', packageName: '@types/react' }),
  dependency({ depName: '@types/react-dom', packageName: '@types/react-dom' }),
  dependency({ manager: 'pep621', datasource: 'pypi', packageFile: 'pyproject.toml',
    depName: 'ruff', packageName: 'ruff', updateType: 'minor' }),
  ...['node', 'python', 'ghcr.io/astral-sh/uv'].map((name) => dependency({
    manager: 'dockerfile', datasource: 'docker', packageFile: 'Dockerfile',
    depName: name, packageName: name, updateType: 'digest' })),
  dependency({ manager: 'dockerfile', datasource: 'docker',
    packageFile: '.github/docker/Dockerfile.browser', depName: 'mcr.microsoft.com/playwright',
    packageName: 'mcr.microsoft.com/playwright', updateType: 'minor' }),
  dependency({ manager: 'docker-compose', datasource: 'docker', packageFile: 'docker-compose.yaml',
    depName: 'example/image', packageName: 'example/image', updateType: 'pinDigest' }),
  dependency({ manager: 'dockerfile', datasource: 'apk', packageFile: 'Dockerfile',
    depName: 'libuuid', packageName: 'libuuid' }),
  dependency({ manager: 'github-actions', datasource: 'github-tags', packageFile: '.github/workflows/ci.yaml',
    depName: 'actions/checkout', packageName: 'actions/checkout', updateType: 'digest' }),
  dependency({ manager: 'pre-commit', datasource: 'github-tags', packageFile: '.pre-commit-config.yaml',
    depName: 'renovatebot/pre-commit-hooks', packageName: 'renovatebot/pre-commit-hooks' }),
  dependency({ manager: 'custom.regex', datasource: 'github-releases',
    packageFile: '.github/actions/scan-image/action.yaml', depName: 'aquasecurity/trivy',
    packageName: 'aquasecurity/trivy' }),
  dependency({ manager: 'custom.regex', datasource: 'pypi', packageFile: 'scripts/security-audit.sh',
    depName: 'pip-audit', packageName: 'pip-audit' }),
];
const branchesFor = (deps, policy = config) => {
  const packageFiles = {};
  for (const { manager, packageFile, updateType, ...dep } of deps) {
    const files = packageFiles[manager] ??= [];
    let file = files.find((file) => file.packageFile === packageFile);
    if (!file) files.push(file = { packageFile, deps: [] });
    file.deps.push({ ...dep, updates: [{ updateType, newVersion: '1.1.0', newValue: '1.1.0' }] });
  }
  return branchifyUpgrades({ ...policy, repoIsOnboarded: true,
    semanticCommits: 'disabled', errors: [], warnings: [] }, packageFiles);
};
let cases = 0;
async function scenario(name, test) {
  await test();
  cases += 1;
  console.log(`PASS ${name}`);
}

await scenario('ordinary timestamped releases wait and require timestamps', async () => {
  for (const datasource of ['npm', 'pypi', 'apk', 'github-tags', 'github-releases']) {
    const update = await effective({ datasource });
    assert.equal(update.minimumReleaseAge, '7 days', datasource);
    assert.equal(checkMinimumReleaseAge(update, recent).isPending, true);
    assert.equal(checkMinimumReleaseAge(update, threeDaysOld).isPending, true);
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
await scenario('every routine ecosystem shares a reviewed weekly group without automerge exceptions', async () => {
  for (const dep of routineDependencies) {
    const update = await effective(dep);
    assert.equal(update.groupName, 'weekly maintenance', dep.depName);
    assert.equal(update.groupSlug, 'weekly-maintenance', dep.depName);
    assert.equal(update.groupSingleUpdates, true);
    assert.equal(update.separateMajorMinor, false);
    assert.equal(update.separateMinorPatch, false);
    assert.equal(update.dependencyDashboardApproval, false);
    assert.equal(update.automerge, false);
    assert.deepEqual(update.schedule, weeklySchedule);
    assert.equal(update.updateNotScheduled, false);
  }
});
await scenario('Renovate generates one stable branch for mixed updates and a single remaining update', async () => {
  for (const deps of [routineDependencies, routineDependencies.slice(0, 1)]) {
    const result = await branchesFor(deps);
    assert.equal(result.branches.length, 1);
    assert.deepEqual(result.branchList, ['renovate/weekly-maintenance']);
    assert.equal(result.branches[0].upgrades.length, deps.length);
    assert.equal(result.branches[0].automerge, false);
  }
});
await scenario('major upgrades and replacements require approval outside the routine group', async () => {
  for (const dep of routineDependencies) {
    for (const updateType of ['major', 'replacement']) {
      const update = await effective({ ...dep, updateType });
      assert.equal(update.dependencyDashboardApproval, true);
      assert.equal(update.groupName, null);
      assert.equal(update.automerge, false);
    }
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
await scenario('standalone lockfile maintenance cannot create a second routine PR', async () => {
  const update = mergeChildConfig(await effective({ updateType: 'lockFileMaintenance' }), config.lockFileMaintenance);
  assert.equal(update.enabled, false);
  assert.equal(update.automerge, false);
  assert.equal(update.minimumReleaseAge, null);
});
await scenario('Node and Python updates stay within supported versions', async () => {
  const node = await effective({ manager: 'dockerfile', datasource: 'docker', packageFile: 'Dockerfile',
    depName: 'node', packageName: 'node' });
  const python = await effective({ manager: 'pep621', datasource: 'python-version', depName: 'python', packageName: 'python' });
  const types = await effective({ depName: '@types/node', packageName: '@types/node' });
  for (const [update, current, versions, expected, scheme] of [
    [node, '23-trixie-slim', ['24-trixie-slim', '25-trixie-slim', '24-alpine', '24-bookworm-slim'], ['24-trixie-slim'], 'docker'],
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
  const releases = ['3.15.0rc1-alpine3.24', '3.15.0-alpine3.24', '3.16.0rc1-alpine3.24'].map((version) => ({ version }));
  assert.equal(compatibility.ignoreUnstable, false);
  assert.equal(compatibility.automerge, false);
  assert.equal(compatibility.dependencyDashboardApproval, true);
  assert.equal(compatibility.groupName, null);
  assert.deepEqual(filterVersions(production, '3.14-alpine3.24', undefined, releases, versioning('docker')), []);
  assert.deepEqual(filterVersions(compatibility, '3.15.0a1-alpine3.24', undefined, releases, versioning('docker')).map((r) => r.version),
    ['3.15.0rc1-alpine3.24', '3.15.0-alpine3.24']);
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
    assert.equal(update.updateNotScheduled, true);
    assert.equal(update.dependencyDashboardApproval, false);
    assert.equal(update.prConcurrentLimit, 0);
    assert.deepEqual(update.labels, ['security']);
    assert.equal(update.vulnerabilityFixStrategy, 'lowest');
    const selected = applyVulnerabilityFixFilter(update, {}, versioning('npm'),
      ['1.0.0', '1.0.1', '1.1.0'].map((version) => ({ version })));
    assert.deepEqual(selected.releases.map((release) => release.version), ['1.0.1']);
    assert.equal((await applyPackageRules(update)).minimumReleaseAge, null);
    const result = await branchesFor(routineDependencies, policy);
    assert.deepEqual(result.branchList.toSorted(),
      ['renovate/npm-react-vulnerability', 'renovate/weekly-maintenance']);
    const security = result.branches.find((branch) => branch.isVulnerabilityAlert);
    assert.equal(security.prConcurrentLimit, 0);
    assert.equal(security.updateNotScheduled, true);
    assert.deepEqual(security.schedule, []);
    assert.equal(security.upgrades.length, 1);
  }
});
await scenario('a major security fix bypasses approval, age and weekly scheduling', async () => {
  platforms.set('github', { getVulnerabilityAlerts: async () => [{
    security_vulnerability: { package: { ecosystem: 'actions', name: 'renovatebot/pre-commit-hooks' },
      first_patched_version: { identifier: '45.0.0' }, severity: 'high' },
    security_advisory: { identifiers: [], summary: 'Fixture', description: 'Fixture', severity: 'high' },
  }] });
  setPlatformApi('github');
  const policy = await detectVulnerabilityAlerts(config);
  const update = await effective({ manager: 'pre-commit', datasource: 'github-tags',
    depName: 'renovatebot/pre-commit-hooks', packageName: 'renovatebot/pre-commit-hooks',
    currentVersion: '44.133.0', currentValue: '44.133.0', updateType: 'major' }, policy);
  assert.equal(update.isVulnerabilityAlert, true);
  assert.equal(update.automerge, false);
  assert.equal(update.minimumReleaseAge, null);
  assert.deepEqual(update.schedule, []);
  assert.equal(update.updateNotScheduled, true);
  assert.equal(update.dependencyDashboardApproval, false);
  assert.equal(update.groupName, null);
});
await scenario('one routine PR respects the weekly window, branch protection and CI', async () => {
  assert.equal(config.prConcurrentLimit, 1);
  assert.deepEqual(config.schedule, weeklySchedule);
  assert.equal(config.updateNotScheduled, false);
  assert.equal(config.rebaseWhen, 'auto');
  assert.equal(config.ignoreTests, false);
  assert.equal(config.platformAutomerge, false);
  assert.equal(config.internalChecksFilter, 'strict');
});
await scenario('the maintenance window is Monday midnight through 6am in the project timezone', async () => {
  for (const [time, expected] of [
    ['2026-11-09T04:59:00Z', false],
    ['2026-11-09T05:00:00Z', true],
    ['2026-11-09T10:59:00Z', true],
    ['2026-11-09T11:00:00Z', false],
    ['2026-11-10T05:00:00Z', false],
    ['2026-07-06T04:00:00Z', true],
  ]) {
    const now = DateTime.fromISO(time).setZone(config.timezone);
    assert.equal(cronMatches(config.schedule[0], now, config.timezone), expected, time);
  }
});
await scenario('pnpm cooldown stays disabled and Python compatibility has one image owner', async () => {
  assert.match(readFileSync('frontend/pnpm-workspace.yaml', 'utf8'), /^minimumReleaseAge: 0$/m);
  assert.doesNotMatch(readFileSync('.github/workflows/next-python.yaml', 'utf8'), /NEXT_PYTHON_IMAGE=/);
  assert.match(readFileSync('.github/docker/Dockerfile.next-python', 'utf8'), /^ARG NEXT_PYTHON_IMAGE=python:/m);
});
console.log(`${cases} effective Renovate policy scenarios passed`);
