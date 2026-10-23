# bambu-spoolman

Track Bambu Lab printer filament usage in [Spoolman](https://github.com/Donkie/Spoolman).
The app estimates usage from the print's G-code and updates Spoolman as each layer
finishes. A web UI maps AMS trays to Spoolman spools.

This is an independently maintained fork of
[`mrkirby153/bambu-spoolman`](https://github.com/mrkirby153/bambu-spoolman),
retaining its Git history and MIT license.

## Quickstart

> [!WARNING]
> The app has no built-in authentication. Keep the web UI and gRPC service on a
> trusted private network, or use an authenticating reverse proxy for remote access.

Download the configuration:

```sh
curl -o .env https://raw.githubusercontent.com/jeversol/bambu-spoolman/main/.env.example
curl -o docker-compose.yaml https://raw.githubusercontent.com/jeversol/bambu-spoolman/main/docker-compose.yaml
```

Set `SPOOLMAN_URL`, `PRINTER_IP`, `PRINTER_SERIAL`, and `PRINTER_ACCESS_CODE` in
`.env`, then start the app:

```sh
docker compose up -d
```

Open `http://<docker-host>:8080` and map your AMS trays to Spoolman spool IDs.
The app must connect to the printer before it can discover the attached AMS units.

## Configuration

Set these values in `.env`. The supplied Compose file stores settings and print
checkpoints in a persistent volume.

| Variable | Purpose / default |
| --- | --- |
| `SPOOLMAN_URL` | Spoolman base URL, reachable from the container. |
| `PRINTER_IP` | Printer IP address. |
| `PRINTER_SERIAL` | Printer serial number. |
| `PRINTER_ACCESS_CODE` | Printer access code. |
| `BAMBU_SPOOLMAN_PORT` | Host port for the web UI; `8080` in the example configuration. |
| `BAMBU_SPOOLMAN_CONFIG` | Configuration directory inside the container; default `/config`. |
| `BAMBU_SPOOLMAN_HTTP_TIMEOUT` | Timeout for Spoolman and printer file HTTP requests; default `30` seconds. |
| `LOGURU_LEVEL` | Log verbosity; default `INFO`. Use `DEBUG` for MQTT summaries or `TRACE` for raw payloads. |
| `SPOOLMAN_VERIFY` | Set to `false` to disable TLS certificate verification for Spoolman. |
| `SPOOLMAN_RFID_FIELD_KEY` | Legacy RFID mapping field key for Spoolman older than v0.27.0; see below. |
| `SPOOLMAN_AUTO_CREATE_SPOOLS` | Set to `true` to create spools for unknown Bambu RFID tags. Requires Spoolman v0.27.0+ or `SPOOLMAN_RFID_FIELD_KEY`. |
| `SPOOLMAN_AMS_FIELD_NAME` | Spoolman custom field for the AMS index. |
| `SPOOLMAN_TRAY_FIELD_NAME` | Spoolman custom field for the tray index. |

### RFID mapping

Spoolman v0.27.0+ uses native tags automatically. The app links a spool's logical
ID and both physical RFID labels, so mappings survive flipping the spool and
work with Spoolman's tag scanner. The second label is learned when first detected.

For older Spoolman versions, create a spool-level Text custom field and set
`SPOOLMAN_RFID_FIELD_KEY` to its exact key, such as `rfid_tag`. The legacy variable
`SPOOLMAN_SPOOL_FIELD_NAME` is also accepted. After upgrading Spoolman, existing
mappings migrate as their spools are encountered; remove the fallback once migration
is complete.

### Custom container deployments

The supplied Compose file includes the required permissions and writable mounts.
For custom Docker or Kubernetes configurations:

- Allow startup as root with `CHOWN`, `SETGID`, and `SETUID`. The container fixes
  data ownership, then runs the app as `10001:10001` without capabilities.
- Do not force `runAsUser` or `runAsNonRoot`.
- Keep `/config`, `/tmp`, and `/app/frontend/.next/cache` writable. The rest of the
  filesystem can be read-only, with privilege escalation disabled.
- If storage denies ownership changes, provision `/config` with ownership
  `10001:10001`.

### Logs

```sh
docker compose logs -f bambu-spoolman
```

Logs include connectivity, print progress, filament consumption, and tray changes.
The `event=service_start` message identifies the running build. `TRACE` logs are
verbose and may include printer and job metadata.

## Development

Run the tests and linters used in CI:

```sh
docker build --target verify .
```

Build and smoke-test the image:

```sh
docker build --tag bambu-spoolman:local .
sh scripts/container-smoke-test.sh bambu-spoolman:local
```

Audit dependencies:

```sh
sh scripts/security-audit.sh
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for the Git and pull request workflow.

## Untested configurations

- External spools.
- LAN-only prints.
- Custom filament or layer-change G-code. Detection uses `M620` for filament
  changes and `M73` for layer changes.
- Multiple AMS units.
