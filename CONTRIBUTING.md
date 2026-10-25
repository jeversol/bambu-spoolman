# Contributing

## Runtime and tool versions

Production, frontend builds, and dependency audits inherit the same Alpine
Node.js package from `runtime_base` in `Dockerfile`. Do not add a separate Node
image or install another Node version in the audit script. The frontend supports
Node 24, enforced by `engines.node` and pnpm's `engineStrict` setting.

| Component | Version source | Automated updates |
| --- | --- | --- |
| Python and Alpine | Version-and-digest-pinned `python_base` in `Dockerfile` | Renovate Dockerfile manager; Python 3.14 and Alpine 3.24 promotions are manual |
| Node, npm, protoc, su-exec, libuuid | `apk add` specifications in `Dockerfile` | Renovate APK datasource, scoped to Alpine 3.24 repositories |
| pnpm | `frontend/package.json` `packageManager` | Renovate npm manager; shared toolchain reads this value |
| Frontend libraries, ts-proto, TypeScript | `frontend/package.json` and `pnpm-lock.yaml` | Renovate npm manager and lockfile maintenance |
| Python libraries, grpcio-tools, Ruff | `pyproject.toml` and `uv.lock` | Renovate Python managers and lockfile maintenance |
| Python wheel build backend | `pyproject.toml` `build-system.requires` | Renovate Python manager |
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
The container logs `event=container_runtime` with its actual Node and Python
versions at startup; the image scan checks installed OS and library packages.

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

## Application consolidation: assignment flow

Spool assignment uses the Python HTTP API. React components and both assignment dialogs remain in place.
The dashboard, external-holder page, and individual tray pages read settings and
inventory through HTTP; assigning and clearing use browser JSON requests.
Successful mutations invalidate the settings cache immediately before refreshing
the page. Dashboard polling continues on its existing interval.

Python owns the assignment rules in `bambu_spoolman/assignments.py`. The new HTTP
handler and transitional gRPC handler both call that service, so locked trays,
duplicate assignments, persistence, and Spoolman synchronization have one owner.
Spoolman 404 means a missing spool; upstream authorization/server/network errors
remain errors instead of appearing as a deleted spool.

| Method | Browser path | Behavior |
| --- | --- | --- |
| GET | `/api/v1/settings` | Current tray mapping and locks |
| GET | `/api/v1/spools` | Spoolman inventory, preserving its JSON fields |
| GET | `/api/v1/spools/<id>` | One spool, or 404 |
| PUT | `/api/v1/trays/<id>` | JSON `{ "spoolId": 42 }`; `-1` clears; 204 on success |

The Python listener binds only `127.0.0.1:50052` in the existing backend process.
Next temporarily forwards the supported routes on the existing UI port; no extra
container, process, published port, or CORS allowance is needed. For separate
local development processes, set `API_SERVER_URL` on Next (default
`http://127.0.0.1:50052`). Keep the same trusted-network deployment boundary.
Mutation requests require JSON, enforce a 4 KiB limit, and reject foreign browser
origins at the proxy. Application errors are JSON; mutation requests are not
retried automatically because a timed-out write may already have completed.

Run the production build and `sh scripts/container-smoke-test.sh <image>`.
The smoke test uses a disposable Spoolman fixture to verify assignment/removal,
dynamic pages, immediate cache invalidation, duplicate errors, and upstream
outages, followed by the existing restricted-runtime/restart checks. Backend
contract tests and frontend HTTP-client tests run in their respective verifier
stages. These checks do not drive a browser's buttons or a physical printer.

The remaining architecture work is serving static React assets from Python and
removing production Node, gRPC, protobuf generation, and dual-process supervision.
The existing best-effort Spoolman location-field synchronization is unchanged;
explicit partial-failure reporting belongs to the subsequent backend work.

## Printer and RFID HTTP API

All UI calls use HTTP. Printer snapshots and Spoolman feature detection use `/api/v1/printer` and `/api/v1/info`. Linking,
temporary override, and permanent unlinking use browser JSON requests through
the same proxy and limits as assignment.

| Method | Browser path | Body |
| --- | --- | --- |
| GET | `/api/v1/printer` | None; connection, last update, raw printer snapshot |
| GET | `/api/v1/info` | None; Spoolman health and feature support |
| PUT | `/api/v1/spools/<id>/rfid` | `{ "uuid": "<tag>" }` links a nonempty tag |
| POST | `/api/v1/trays/<id>/rfid/override` | `{ "uuid": "<current tag>" }` pauses automatic mapping |
| POST | `/api/v1/trays/<id>/rfid/unlink` | `{ "spoolId": 42, "uuid": "<current tag>" }` permanently unlinks |

`bambu_spoolman/rfid.py` owns RFID rules for HTTP and the transitional gRPC
adapter. Permanent unlink is one server-coordinated operation: check the expected
assignment, suppress relinking for this insertion, then remove the association.
A stale assignment returns 409; a missing/currently changed tag is rejected. If
only part succeeds, the response describes that partial result and retains the
local override to prevent immediate automatic recreation. Writes are not retried
automatically. Settings, spool, and printer caches are invalidated even on failed
mutations because one part may already have applied.

Upstream feature lookups and RFID synchronization run outside the event loop so
status polling remains responsive. The automatic switch uses one reentrant lock
for MQTT synchronization, manual refresh, and override of its shared state.
This does not provide a transaction across local settings and Spoolman; broader
mapping concurrency and lifecycle work remains in the backend-refactor stage.

The frontend no longer has a gRPC client or transport dependencies. TypeScript
protobuf generation emits data models only during this transition. The old
backend adapter and generated UI presentation types remain until static React
assets can be served directly by Python, at which point Node, gRPC, and protobuf
build steps can be removed together. The container smoke test deliberately points
the old frontend gRPC URL at an unavailable address to verify HTTP independence.

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
