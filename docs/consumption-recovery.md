# Recover paused consumption accounting

The broker pauses accounting if a usage increment may have reached Spoolman but
its outcome is unknown. It never infers success or failure from aggregate spool
totals: another writer can increase or reset those totals. A pending request
survives restart, failed/cancelled print status and new print announcements.
Printer status and mapping remain available; the UI warns that usage totals may
be incomplete. Automatic consumption stays paused until operator recovery.

The current [Spoolman usage API](https://github.com/Donkie/Spoolman/blob/master/spoolman/api/v1/spool.py)
accepts an increment, without a request identifier/deduplication contract. A local
UUID identifies our intent but is not server-side idempotency. This policy does
not claim exactly-once delivery. Preflight reads and never-issued requests can
retry; an exception after invoking consumption is conservatively uncertain,
including a server/proxy error. A successful response observed by the running
process can be acknowledged without sending another increment. If that success
was not persisted before a crash, recovery requires operator evidence.

## Inspect and resolve

1. Stop the broker before changing its accounting state. Keep the original
   checkpoint/model and service logs. Let the original upstream request finish
   or establish that it can no longer commit before authorizing a retry or making
   a compensating adjustment.
2. Verify the intended Spoolman server, spool, print and usage interval from
   independent evidence, such as a correlated server request/transaction log.
   Equal, higher or lower aggregate totals alone are insufficient. If certainty
   is unavailable, keep accounting paused. The request resolver addresses one
   increment, not an arbitrary gap in printer history. Choose the recovery path
   below before making any manual adjustment to totals.
3. Inspect status using the same Compose configuration/volume as the broker:

```sh
docker compose stop bambu-spoolman
docker compose run --rm bambu-spoolman /venv/bin/python -m bambu_spoolman.broker.consumption_recovery status
```

Copy the complete `pending.intentId` token and its `spoolId`. Then choose exactly
one decision. Replace the placeholders; keep the quotes around the evidence note.

If the increment was applied (or you have independently reconciled that exact
increment in Spoolman), acknowledge it without another network write:

```sh
docker compose run --rm bambu-spoolman /venv/bin/python -m bambu_spoolman.broker.consumption_recovery resolve --intent-id TOKEN --spool-id 42 --decision applied --note 'Evidence identifying the applied request'
```

If the increment definitely was not applied and cannot still commit, authorize
one new attempt for the saved original spool and amount:

```sh
docker compose run --rm bambu-spoolman /venv/bin/python -m bambu_spoolman.broker.consumption_recovery resolve --intent-id TOKEN --spool-id 42 --decision not-applied --note 'Evidence proving the request did not apply and cannot still commit'
```

The command never modifies Spoolman. It requires the broker to be stopped,
checks the exact pending-record/print token, spool and saved server scope, and
creates a recovery backup under `consumption-recovery/` before updating the
checkpoint atomically. A stale token cannot resolve another request or repeat
a previous decision. Inspect and archive those backups with your service logs.
The backup records the *requested* decision; an interrupted/failed checkpoint
write does not claim it committed. Reinspect status after any command failure.
Legacy intents have no recorded server scope: explicitly verify the historical
server before resolving them; the decision binds future delivery to the current
`SPOOLMAN_URL`. Never delete a pending intent merely to restart tracking.

### Resume the original checkpoint

Use this path only if its saved mapping/object-skip history remains valid for
all unaccounted extrusion. Verify that printer activity during the pause did not
introduce missing mapping, refill or object-skip transitions. Resolve only the
pending increment as above. **Do not also manually charge the checkpoint's
remaining layers**: normal catch-up will charge them after restart. If you have
already corrected the entire print, or cannot verify the missing history, use
the archive path instead.

After verifying this request-only recovery and the validity of the saved history:

```sh
docker compose up -d bambu-spoolman
```

The broker resumes a matching checkpoint when the printer reports that print.
A confirmed intent is acknowledged locally; an authorized unsent intent targets
its saved original spool even if tray assignments changed. A fresh uncertain
attempt pauses again. Restart does not clear an uncertainty. The tool cannot
reconstruct lost printer history or verify the operator's evidence.

### Close an ended print after reconciling all usage

Keep the service stopped. First finish/cancel the physical print and ensure the
original request can no longer commit. Independently reconcile the complete old
print and any paused interval in Spoolman, coordinating other writers. Resolve
the pending request as `applied` once its increment is represented in those
corrected totals. Then use the **new** `pending.intentId` from that command's
output (the old token is invalid) to archive the ended print:

```sh
docker compose run --rm bambu-spoolman /venv/bin/python -m bambu_spoolman.broker.consumption_recovery archive-print --intent-id NEW_TOKEN --print-ended --all-usage-reconciled --note 'Ended print and all spool usage independently reconciled'
```

This command refuses unknown requests, requires explicit operator assertions,
and moves the entire checkpoint/model into a uniquely named recovery archive
under the same configuration volume. It performs no Spoolman writes. Failed
renames leave the original checkpoint in place. Preserving the archive avoids
replaying backlog you already corrected manually. Restart for a new print only
after the old one has ended; do not resume the archived print. An ambiguous
request with no adequate evidence must remain paused.

## Persistence and exclusive ownership

The broker holds a local file lease for its runtime. The recovery command takes
the same lease before mutation, preventing online edits by the provided tool and
rejecting a second broker against the same local configuration. This is not a
distributed lock for independent volumes/hosts. All use of these files must obey
the stopped-service recovery procedure.

New intents pass through `prepared`, `in_flight` and `confirmed`. The transition
to `in_flight` is saved before calling Spoolman. A crash in that window is
conservatively uncertain even if no request was actually sent. Legacy intents
without a phase are also uncertain. Prepared work is known unsent or explicitly
authorized by recovery; confirmed work has observed success or an operator
decision. The numeric baseline remains diagnostic only and never authorizes
replay or acknowledgment. Completion marks and intent clearance commit in one
checkpoint update; failed acknowledgment writes restore the in-memory pending
state. Live, in-progress requests do not falsely report a recovery pause.
