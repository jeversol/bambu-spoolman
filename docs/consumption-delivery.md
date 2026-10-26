# Consumption delivery and checkpoint ownership

The tracker coordinates printer lifecycle, planning, mappings and retry timers.
`broker/consumption.py` delivers a planned layer through the `ConsumptionClient`
and `ConsumptionJournal` protocols. It does not access MQTT, parse models, read
settings or create timers. A completed layer is returned to the tracker, which
marks it complete and chooses when to advance or retry.

`broker/print_checkpoint.py` owns `ConsumptionProgress`: acknowledged layers,
filaments and physical segments, plus the one pending consumption intent.
Its context callback provides printer/mapping position without exposing the
tracker to the delivery component. `PrintCheckpoint` snapshots progress for the
filesystem adapter in `broker/checkpoint.py`, creates checkpoints, adapts legacy
recovery results, and clears or marks a checkpoint started. The persisted schema
and legacy disk recovery rules remain unchanged.

Before incrementing Spoolman usage, delivery saves a pending intent. It saves
acknowledgments after the request succeeds. If that save fails, it restores the
pending intent and removes unpersisted completion marks, including partial
refill segments belonging to the same filament. Reconciliation acknowledgment
failures also retain the pending intent in memory and on disk. Stop prevents a
new segment while allowing the active operation to finish its acknowledgment.
The tracker lock serializes the component's use by messages and retry callbacks;
the components do not create their own concurrent writers.

Unknown outcomes now pause accounting instead of being inferred from aggregate
spool totals. The delivery journal distinguishes prepared, in-flight and confirmed
work; legacy unphased intents require explicit recovery. See
[consumption recovery](consumption-recovery.md) for the state contract and operator
procedure. The broker and offline recovery tool share a local file lease.
