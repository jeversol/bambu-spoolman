# bambu-spoolman

BambuLab integration for Spoolman.

This program will monitor a Bambulab printer and synchronize usage automatically to [Spoolman](https://github.com/Donkie/Spoolman). It does this by listening for prints to be started, parsing the gcode and estimating the filament usage per layer. As layers are completed, the usage for that layer will be pushed to Spoolman.

This project was originally derived from
[`mrkirby153/bambu-spoolman`](https://github.com/mrkirby153/bambu-spoolman) and
retains its Git history and MIT license. It is now independently maintained for
a personal deployment and is not intended to remain synchronized with the
original project.

## Quickstart

```sh
curl -o .env https://raw.githubusercontent.com/jeversol/bambu-spoolman/main/.env.example
curl -o docker-compose.yaml https://raw.githubusercontent.com/jeversol/bambu-spoolman/main/docker-compose.yaml
```

Update `.env` with the appropriate settings. See below for a list of configuration options.

Once the `.env` file is updated, start the app with `docker compose up -d`

## Security

> [!WARNING]
> Bambu Spoolman does not provide built-in user authentication. Deploy it only
> on a trusted private network. Do not expose the web UI or gRPC service
> directly to the Internet. Use an authenticating reverse proxy if access
> outside the trusted network is required.

## Configuration

Set the following environment variables:

* `SPOOLMAN_URL` -- The base URL for your spoolman instance (i.e. `http://localhost:7912`)
  * `SPOOLMAN_VERIFY` -- Set to `false` to disable SSL verification for spoolman requests (Useful for self-signed certificates)
* `BAMBU_SPOOLMAN_HTTP_TIMEOUT` -- Timeout in seconds for Spoolman and printer file HTTP requests (default: `30`)
* `LOGURU_LEVEL` -- Container log verbosity (`INFO` by default; use `DEBUG` for per-message summaries or `TRACE` for raw MQTT payloads)
* `PRINTER_IP` -- The IP address of your printer
* `PRINTER_SERIAL` -- The serial number of your printer
* `PRINTER_ACCESS_CODE` -- The access code for your printer
* `BAMBU_SPOOLMAN_CONFIG` -- A directory to store the configuration file
* `SPOOLMAN_RFID_FIELD_KEY` -- Exact **Key** of the Spoolman spool-level Text custom field where Bambu RFID UUIDs are saved (for example, `rfid_tag`). This enables linking detected tags to existing spools and automatic slot mapping. The legacy name `SPOOLMAN_SPOOL_FIELD_NAME` is still accepted.
* `SPOOLMAN_AUTO_CREATE_SPOOLS` -- Create a matching Spoolman spool when an unknown Bambu RFID tag is detected. RFID mapping must also be configured with `SPOOLMAN_RFID_FIELD_KEY`.
* `SPOOLMAN_AMS_FIELD_NAME` -- Spoolman field to store which AMS a spool is in
* `SPOOLMAN_TRAY_FIELD_NAME` -- Spoolman field to store which tray a spool is in

## Usage

Once deployed, the web ui can be used to configure the mapping of AMS spool trays -> Spoolman spool ids. An initial connection to the printer is needed to determine the number of AMS systems attached.

## Logging

Container logs use searchable `event=... key=value` messages at `INFO` for MQTT connectivity, print lifecycle, layer accounting, successful filament consumption, checkpoints, AMS/RFID changes, and tray assignments. Set `LOGURU_LEVEL=DEBUG` for MQTT message summaries and processing times, or `TRACE` for complete MQTT payloads. Raw payloads are especially verbose and may contain printer/job metadata.

Each container logs its release/ref, CI build number, Git revision, and build timestamp in the `event=service_start` message. Locally built images use `version=local build_number=local` unless those Docker build arguments are supplied explicitly.

## Dependency and image security

Production builds use frozen Python and pnpm lockfiles, version-and-digest-pinned base images, and commit-pinned GitHub Actions. Before an image is published, the workflow runs backend tests and linting, builds and lints the frontend, audits both dependency graphs, smoke-tests the assembled container, and rejects high or critical findings in the final image. Published images include an immutable `sha-<commit>` tag, an SBOM, and provenance metadata.

Renovate proposes weekly npm, Python, Docker, security-tool, and GitHub Actions updates against the `main` branch. Routine patch and minor updates are grouped after a short cooldown; major updates remain separate for explicit review. Python and Node container images stay on the minor or LTS major supported by the project, so runtime upgrades are deliberate changes that also update project constraints, lockfiles, and hard-coded runtime paths. Coupled framework packages are updated together, and the pnpm version in `frontend/package.json` is the single source used by both image builds and dependency audits. A weekly workflow rescans both the deployed lockfiles and the published `latest` image because newly disclosed vulnerabilities can affect an image that was clean when built.

### Python version policy

Production supports one Python minor at a time and uses an immutable container
digest. Renovate refreshes that digest under the supported minor tag, providing
Python patch releases and Alpine security updates without making builds
unrepeatable. Those digest-only refreshes may automerge after CI; changing the
supported Python minor may not.

The `Test next Python` workflow runs weekly against the next Python release on
Alpine. During the prerelease period its scheduled result is an early warning
and does not block other delivery. Before promoting a new minor, run that
workflow manually; a manual failure is a release blocker.

Promote a Python minor in one reviewed pull request:

1. Change the production base image and digest in `Dockerfile`.
2. Update `requires-python` in `pyproject.toml` and regenerate `uv.lock` with
   the new interpreter.
3. Update any version-specific runtime paths in `Dockerfile`.
4. Move both Python `allowedVersions` rules in Renovate to the new minor.
5. Run the normal CI pipeline and a strict manual `Test next Python` workflow
   while it still targets the candidate minor.
6. After promotion, move `.github/docker/Dockerfile.next-python` and its
   workflow build argument to the subsequent Python release.

Do not merge a Renovate change that updates only one of those locations. A new
Python minor is an application migration, not a routine dependency bump.

Run the current dependency audits locally with:

```sh
sh scripts/security-audit.sh
```

## Development

Run the same tests and linters used in CI with Docker:

```sh
docker build --target verify .
```

Build and smoke-test the application image with:

```sh
docker build --tag bambu-spoolman:local .
sh scripts/container-smoke-test.sh bambu-spoolman:local
```

## Untested things

* External spools
* LAN only prints
* Custom filament/layer change gcode (`M620` is used to detect filament changes and `M73` is used to detect layer changes)
* More than 1 AMS unit (I only have one, but this should support multiple AMS units)
