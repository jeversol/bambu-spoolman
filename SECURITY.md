# Security Policy

## System and Scope

Bambu Spoolman is intended for personal deployment on a trusted private
network. The web UI allows operators to inspect printer state and modify tray,
spool, and RFID mappings. Its colocated Python service communicates with the
printer and Spoolman.

The supported deployment does not expose the web UI or gRPC service directly
to the Internet. Access from outside the trusted network requires an
authenticating reverse proxy or equivalent access-control layer.

## Threat Model and Trust Boundaries

The operator, hosts permitted on the private network, deployment
configuration, and configured printer and Spoolman endpoints are trusted.
Printer messages, downloaded print files, dependency registries, and software
changes are treated as potentially untrusted inputs.

The default web UI has no built-in user authentication. Anyone who can reach
it can perform its supported state-changing operations. The internal gRPC
service is intended only for the colocated frontend and must not be published
or otherwise made reachable from an untrusted network.

## Security Invariants

- Printer credentials must not be exposed through source, images, logs, or
  browser responses.
- The web and gRPC interfaces must remain within the trusted deployment
  boundary unless an external authentication layer protects them.
- Untrusted printer data and print files must not grant filesystem or process
  execution capabilities or consume unbounded resources.
- Dependency updates must preserve compatible runtime, lockfile, and tool
  versions.
- Published images must be the exact artifacts that passed required tests and
  vulnerability scans.

## Reportable Findings and Severity Context

Internet exposure of the unauthenticated UI or gRPC service is a deployment
misconfiguration rather than a vulnerability in the supported deployment.
Missing built-in UI authentication alone is an accepted risk and should be
reported as low severity when the trusted-private-network boundary is
established.

Authentication or authorization bypasses remain reportable when an
authenticating proxy or another documented access-control layer is present.
Ways to cross the documented network boundary, expose credentials, perform
unauthorized code execution, corrupt protected state from outside the trusted
boundary, bypass supply-chain controls, or cause material resource exhaustion
are reportable.

## Accepted Risk and Compensating Controls

Built-in UI authentication is intentionally absent for the supported personal,
trusted-network deployment. Network isolation is the compensating control.
Operators are responsible for firewall rules, VLAN or equivalent network
segmentation, reverse-proxy configuration, and ensuring that container port
50051 is not published.

Compromise of an already trusted operator or trusted internal host is outside
the authentication boundary unless it provides an additional privilege or
crosses another security boundary.

## Dependency Vulnerability Enforcement

Dependabot supplies vulnerability alerts, and Renovate is the only automated
dependency-update pull-request generator. Renovate alone controls release age
and security-update age exceptions; pnpm's minimum-release-age check remains
disabled. Dependabot security updates remain disabled to prevent duplicate PRs.
Renovate vulnerability updates require human review rather than automerge.

### Pull requests

- Backend tests, frontend checks, candidate-image construction, container smoke
  testing, dependency reporting, and dependency review run independently where
  their inputs permit. An audit finding does not prevent correctness tests.
- The required `verify` aggregate fails when a required job fails, is cancelled,
  is missing, or unexpectedly skips. `dependency-review` remains a separate
  required check and rejects new high/critical dependency findings in runtime,
  development, and unknown scopes.
- `image-security` compares high/critical OS and library findings against the
  exact PR base using the same Trivy version and vulnerability-database snapshot.
  It prefers the published SHA image after checking its embedded revision,
  falling back to building the base application without running its old CI gates.
- Only unchanged findings already present at the base may pass the PR image
  gate. Finding identity includes ecosystem, location, package, installed
  version, advisory, and severity. A different affected version, location, or
  increased severity is new risk and blocks, even without an available fix.
- Missing, malformed, unsupported, or incomplete reports, failed baseline builds,
  and failed scans fail the check. A failed baseline never means a clean baseline.
  Reports retain existing, introduced, and resolved findings for operator review.
- Complete Python and npm audits are diagnostic on PRs; findings or audit
  service failures appear in their logs and summary. They do not replace either
  required security gate. Existing findings remain remediation work and are
  not classified as safe simply because a PR can merge.

This allows partial remediation and unrelated corrections to merge without
introducing additional risk. Passing PR checks does not authorize publication
of an image that still violates the release policy.

### Releases and manual verification

- Publishing continues to block all high/critical findings in the production
  image, including OS and runtime libraries and findings without a patched version.
- Publishing also enforces high/critical npm runtime findings and critical npm
  findings anywhere in the locked dependency graph. Other development findings
  remain visible through complete audits and Dependabot.
- Manual CI verification uses absolute image and npm thresholds, without a PR
  baseline. Its intentional dependency-review skip is the only allowed skip.
- Python audit findings remain diagnostic because pip-audit has no suitable
  severity threshold; the production-image scan enforces Python runtime severity.
- Public image tags are updated only after the candidate digest passes the
  existing release tests, smoke test, and absolute vulnerability checks.

An audit exception must be limited to a specific advisory and document its
scope, exploitability, compensating controls, and review date. Broad exceptions
such as ignoring every unfixable advisory are not permitted.
