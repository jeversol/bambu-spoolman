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
| Trivy binary | Annotated `version` inputs in security workflows | Renovate GitHub Actions manager, independently of the action version |
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
