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
