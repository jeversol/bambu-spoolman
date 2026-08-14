# Reliability audit changelog

- Based lifecycle handling on the observed Bambu MQTT contract: X1 printers send full `push_status` payloads, while P1 printers send deltas that must be merged; `pushing.pushall` requests a complete snapshot.
- Moved model downloads, G-code parsing, settings writes, and Spoolman calls off Paho's MQTT network thread while preserving message order, preventing slow work from starving the five-second keepalive.
- Split print announcement from confirmed print start. A `FINISH` report is ignored until the announced job has entered `RUNNING` or `PAUSE`, fixing the stale-finish race seen in the pod logs.
- Track task/subtask identity and ignore duplicate `project_file` announcements so an MQTT duplicate cannot reset progress and consume filament twice.
- Recognize the protocol's `FAILED` state (while retaining legacy `FAILURE` compatibility) and the cancellation error code in either numeric or string form.
- Treat Bambu Studio's `M73 L<n>` marker as the start of layer `n`: only earlier layers are charged, while a true `FINISH` charges the final completed layer.
- Improved G-code accounting for relative and absolute extrusion (`M83`, `M82`, and `G92`), retractions/unretractions, sparse logical filament IDs, and Bambu Studio's own total-filament metadata.
- Reconcile per-layer estimates to the slicer's total filament length when the metadata is valid, retaining the layer distribution while matching the authoritative total.
- Persist successful consumption per filament, retain failed layers for retry, and keep the checkpoint after a `FINISH` until every Spoolman update succeeds.
- Added atomic checkpoint writes, persisted lifecycle state, normalized task identifiers and AMS mappings, and kept backward-compatible checkpoint recovery.
- Made settings updates atomic and serialized read-modify-write operations so MQTT and gRPC updates cannot silently overwrite each other.
- Made cached printer status thread-safe and fixed recursive delta merging when a field changes between scalar and object forms.
- Made automatic RFID/AMS switching consume the merged printer snapshot, remove mappings for genuinely removed trays, normalize legacy lock IDs, and clean stale tray metadata.
- Fixed locked-tray validation, duplicate spool assignment checks, integer/string legacy IDs, external-holder tray metadata, and blocking Spoolman calls in the async gRPC server.
- Fixed the Spoolman client URL handling, response validation, consumption argument validation, tray-field update request, timeout handling, external color normalization, and malformed tray metadata handling.
- Fixed frontend stale-cache behavior after clearing a tray, a tray-route off-by-one/NaN validation bug, a falsey spool-ID check, and small configuration/documentation typos.
- Replaced unsafe `eval` in MQTT replay with `ast.literal_eval` and removed dead WSGI/runtime dependencies.
- Made container dependency installation honor both Python and pnpm lockfiles, so the tested dependency set is the set shipped in the image.
- Added regression coverage for lifecycle races, retries, per-filament checkpoints, MQTT dispatch, delta merging, G-code modes/retractions/totals, checkpoint compatibility, settings concurrency, AMS removal, and Spoolman validation.
- Added Loki-friendly `event=... key=value` operational logs at `INFO` for service startup, MQTT connectivity, print lifecycle, model loading, layers, filament consumption, checkpoint recovery, AMS/RFID changes, and tray assignments. `DEBUG` adds MQTT summaries and timing; `TRACE` includes raw MQTT payloads.
- Known boundary: Spoolman's consume endpoint has no idempotency key. A process or host crash in the tiny interval after Spoolman accepts a request but before the local checkpoint is durably replaced can still duplicate that one consumption; the per-filament checkpoint narrows but cannot eliminate this distributed-systems window.

Protocol references: [OpenBambuAPI MQTT notes](https://github.com/Doridian/OpenBambuAPI/blob/main/mqtt.md), [Bambu Studio source](https://github.com/bambulab/BambuStudio), and [Home Assistant Bambu Lab integration source](https://github.com/greghesp/ha-bambulab).
