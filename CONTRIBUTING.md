# Contributing

## Runtime and tool versions

Production runs Python and compiled React assets on one HTTP listener. Node.js
is installed only in the shared `frontend_toolchain` build/audit stage. Its Alpine
package is pinned in `Dockerfile`; do not add another Node image to the audit
script. Node 24 is enforced by `engines.node` and pnpm's `engineStrict` setting.

| Component | Version source | Automated updates |
| --- | --- | --- |
| Python and Alpine | Version-and-digest-pinned `python_base` in `Dockerfile` | Renovate Dockerfile manager; Python 3.14 and Alpine 3.24 promotions are manual |
| Node, npm, su-exec, libuuid | `apk add` specifications in `Dockerfile` | Renovate APK datasource, scoped to Alpine 3.24 repositories |
| pnpm | `frontend/package.json` `packageManager` | Renovate npm manager; shared toolchain reads this value |
| Frontend libraries, TypeScript | `frontend/package.json` and `pnpm-lock.yaml` | Renovate npm manager and lockfile maintenance |
| Python libraries, Ruff | `pyproject.toml` and `uv.lock` | Renovate Python managers and lockfile maintenance |
| Python wheel build backend | `pyproject.toml` `build-system.requires` | Renovate Python manager |
| Browser smoke tools | Pinned Playwright image in `.github/docker/Dockerfile.browser` | Renovate Dockerfile manager |
| uv | Pinned image references in both Dockerfiles | Renovate Dockerfile manager |
| pip-audit | Version pin in `scripts/security-audit.sh` | Renovate custom regex manager |
| Trivy binary | Annotated `version` input in `.github/actions/scan-image/action.yaml` | Renovate GitHub Actions manager, independently of the action version |
| Renovate validator and policy engine | `.pre-commit-config.yaml` Renovate hook revision | Renovate pre-commit manager; CI reads that same pin |
| GitHub Actions and pre-commit hooks | SHA/version references in workflows and `.pre-commit-config.yaml` | Renovate built-in managers |

Renovate alone controls minimum release age. Keep pnpm's `minimumReleaseAge`
at `0`; container builds install the reviewed lockfile without a second cooldown.

TypeScript 6 is intentionally installed under the `typescript` name using the
`@typescript/typescript6` alias: the current ESLint parser requires TypeScript
below 6.1 and tools use its JavaScript API. Do not add a second compiler unless
a dedicated compatibility check exercises it.

The Python base explicitly names its Alpine release so package lookups match
the repositories used by the image. When promoting Alpine, update both that tag
and Renovate's APK registry URL together. Alpine can remove superseded package
versions; update an unavailable pin to the supported version from that same
release rather than switching to `edge` or removing the version constraint.

For a version change, run `docker build --target verified_app .`,
`sh scripts/security-audit.sh gate`, and the production container smoke test.
The container logs `event=container_runtime` with its Python version at startup; the image scan checks installed OS and library packages.

## Renovate policy

Run `sh scripts/validate-renovate.sh` before proposing policy changes. CI runs
it as an independent job required by `verify`. It uses the same pinned Renovate
version as `.pre-commit-config.yaml`, validates the configuration strictly, and
tests the effective policy after resolving inherited presets with Renovate's own
rule matcher, release-age checks, and GitHub-alert conversion. Updating that hook
also updates the validator and policy-test engine; there is no second version pin.
The command runs inside the project's shared container toolchain.

Ordinary npm, PyPI, Alpine, GitHub tag, and GitHub release updates wait at least
one day. Missing timestamps hold those updates rather than bypassing the wait.
Docker updates, digest changes, and initial pins have no age guarantee: registry
timestamps may be unavailable, and a digest update does not establish when the
new content was released. These updates still run the required checks.

Security updates at every severity bypass cooldowns and schedules, select the
lowest fixed version, and require human review. This makes Renovate's existing
inherited behavior explicit; the previous critical-only JSONata exception did
not restrict the default bypass. Security updates are separate from normal groups.

Automerge is limited to minor/patch updates of the Renovate validator hook and
digest updates of the production Python base. The former runs the updated
validator and policy scenarios; the latter runs backend/frontend checks and
container smoke/security checks. Application libraries, Alpine packages, other
images, GitHub Actions, compatibility Python, and major updates require review.
Renovate rebases whenever its branch falls behind `main`; GitHub also requires
an up-to-date branch before merging.

Weekly lockfile maintenance remains a reviewed PR. Renovate delegates resolution
of indirect dependencies to the package manager, so minimum release age does not
cover lockfile maintenance or every transitive change in an ordinary update.
pnpm's second cooldown remains disabled by operator policy. Review lockfile
changes alongside the dependency review and image scan.

Next-Python compatibility takes its image solely from the `NEXT_PYTHON_IMAGE`
default in `.github/docker/Dockerfile.next-python`; the workflow must not override
it. Its updates stay within the chosen Python 3.15 compatibility minor and may
include prereleases. Production Python promotion remains a separate reviewed change.

References: [Renovate release age](https://docs.renovatebot.com/key-concepts/minimum-release-age/)
and [configuration options](https://docs.renovatebot.com/configuration-options/).

## Application architecture and development

The production container runs one Python process as PID 1, serving the compiled
React dashboard and `/api/v1` on port 3000. Node, Next.js, internal gRPC/protobuf,
and shell process supervision are removed. React Router preserves `/`,
`/external-spool`, and `/ams/<ams>/tray/<slot>` deep links. Python serves HTML
with revalidation and hashed assets with immutable caching. Files outside the
static root, dotfiles, missing assets, and unknown API routes never receive the
application HTML fallback. At startup Python inventories the built assets; URLs
only select entries from that allowlist, never construct filesystem paths.
Symlinks are excluded. Rebuild/restart to publish a changed asset set.

Vite builds the UI; TypeScript 6 (`tsc6`) and ESLint run before release. For
separate development processes, run `uv run bambu-spoolman-broker` with the
normal printer/Spoolman environment, then `pnpm dev` in `frontend`. Vite proxies
`/api` to Python at `127.0.0.1:3000`. Build the UI first and set
`BAMBU_SPOOLMAN_STATIC` to its absolute `frontend/dist` path to serve it directly
from a local Python process.

TanStack Query owns one coherent settings/printer/inventory/features snapshot.
Successful and failed writes invalidate reads because partial changes may have
applied; writes are never automatically retried. The dashboard polls every five
seconds while visible and pauses while a mapping dialog is open. Initial errors
and background refresh failures expose a retry button; background failures retain
the last good snapshot. Font assets are bundled locally.

Python owns assignment and RFID rules in `assignments.py` and `rfid.py`.
Mutations require JSON, enforce a 4 KiB body limit, and reject foreign browser
origins. The trusted-private-network deployment boundary remains unchanged.

| Method | Path | Behavior |
| --- | --- | --- |
| GET | `/api/v1/settings` | Tray mappings and locks |
| GET | `/api/v1/printer` | Connection and raw printer snapshot |
| GET | `/api/v1/info` | Spoolman health and tray-locking feature support |
| GET | `/api/v1/spools` and `/api/v1/spools/<id>` | Raw Spoolman inventory |
| PUT | `/api/v1/trays/<id>` | `{ "spoolId": 42 }`; `-1` clears |
| PUT | `/api/v1/spools/<id>/rfid` | `{ "uuid": "<tag>" }` links a nonempty tag |
| POST | `/api/v1/trays/<id>/rfid/override` | `{ "uuid": "<current tag>" }` pauses automatic mapping |
| POST | `/api/v1/trays/<id>/rfid/unlink` | `{ "spoolId": 42, "uuid": "<current tag>" }` permanently unlinks |

The UI's HTTP presentation model explicitly converts Spoolman JSON, preserving
weight defaults, multicolor data, and custom extra-field keys. There is no UI
code generation. The image retains metadata for the production npm dependency
closure under `/app/frontend-inventory`, outside the web root, so image scans
and SBOMs still cover browser libraries without shipping a Node executable.

`sh scripts/container-smoke-test.sh <image>` uses a disposable Spoolman fixture
and pinned Playwright/Chromium image to exercise actual assignment/removal,
deep-link reloads, history, themes, paused polling, and outage recovery. It also
checks API contracts, privileges, read-only filesystems, persistence, restart,
Python PID 1, absence of Node/internal listeners, and graceful shutdown. Browser
tool versions are owned by `.github/docker/Dockerfile.browser`; its driver
version is derived from the same image tag. No live printer is needed.

MQTT retry waits are interruptible during shutdown. Compose allows 45 seconds
for in-flight upstream requests and HTTP cleanup before forcing termination. The broader ownership of
accounting tasks, timers, downloads, and concurrent mapping changes remains in
the next backend-refactor step; this change does not provide a distributed
transaction across settings and Spoolman.

## CI checks and vulnerability remediation

The required `verify` result aggregates backend tests, frontend checks, the
candidate image build, smoke testing, image security, dependency reporting,
and dependency review. Tests run even when an independent audit fails. A
failed, cancelled, missing, or unexpectedly skipped required job fails `verify`.

PR dependency review blocks new high/critical dependency findings in every
scope. The image gate rescans the exact base and candidate using the same
database snapshot and blocks new high/critical findings. Existing unchanged
findings remain visible, allowing incremental repairs to merge. A changed
affected package version or location is new risk. Baseline acquisition or scan
failures block the comparison; rerun after restoring the baseline or scanner,
rather than skipping it. Scan JSON reports remain available as workflow artifacts.

The baseline normally comes from the published `sha-<base commit>` image with
its revision label verified. If publication was unavailable, CI builds the exact
base's `app` target without relying on its old test gates.

Full dependency audits are diagnostic on PRs. Their failure can mean an advisory
or an audit/tool/network error; inspect the audit logs and summary. Publishing
and manual verification still enforce absolute vulnerability thresholds, so a
merged repair may leave publication blocked until the remaining findings are fixed.

To run the independent correctness checks locally:

```sh
docker build --target backend_verifier .
docker build --target frontend_verifier .
docker build --target runtime_checked_app --tag bambu-spoolman:local .
sh scripts/container-smoke-test.sh bambu-spoolman:local
```

`docker build --target verified_app .` still requires all verification stages
and remains suitable for checking a complete change before publishing.

## Release and deployed-image verification

Publishing builds `verified_app` once, including backend tests and frontend
checks. The dependency gate runs independently; the smoke test and image scan
independently consume the resulting immutable digest. Only successful gates
allow promotion. Promotion preserves that digest, verifies the commit tag first,
and updates `latest` last. A failed verification gate leaves `latest` unchanged.
Release runs are serialized without cancelling an in-progress promotion.

The smoke test uses the Compose read-only filesystem, capabilities, temporary
filesystems, and privilege restrictions. With a disposable configuration volume,
it checks frontend and backend API readiness, migration of root-owned files,
settings and checkpoint persistence across restart, and graceful shutdown.
It requires no live printer or Spoolman instance.

Scheduled scans resolve the published digest and source revision once. Dependency
gating, complete dependency reporting, and image scanning then run independently;
the dependency checks use that deployed revision's lockfiles even when `main`
has advanced. Image scans in CI, publishing, and scheduled checks share one
scanner configuration. JSON evidence is retained for 14 days, including on gate
failure; unavailable or malformed evidence fails the image gate.

## Git workflow cheat sheet

Create a branch for every change so GitHub can run the required checks before
the change reaches `main`.

### Start a change

```bash
git switch main
git pull --ff-only origin main
git switch -c fix/short-description
```

Use a short branch name such as `fix/spool-sync`, `feat/printer-status`, or
`chore/update-dependencies`.

### Save and publish the change

```bash
git status
git add path/to/changed-file
git commit -m "fix: describe the change"
git push -u origin HEAD
gh pr create --base main --fill
```

`git push -u origin HEAD` works with any branch name. After the first push,
plain `git push` is enough.

### Check CI

```bash
gh pr checks --watch
```

If a check fails, fix it on the same branch, then run:

```bash
git add path/to/changed-file
git commit -m "fix: address CI failure"
git push
```

The existing pull request updates and CI runs again. Merge it on GitHub after
all required checks pass.

### After merging

```bash
git switch main
git pull --ff-only origin main
git branch -d fix/short-description
```

Replace `fix/short-description` with the branch you used.

### If you accidentally commit on `main`

Do not try to force-push. Create a branch at the commit and push that branch:

```bash
git switch -c fix/short-description
git push -u origin HEAD
gh pr create --base main --fill
```

Then ask for help resetting the local `main` branch if needed; the commit is
safe on the new branch.
