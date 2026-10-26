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

This extraction preserves the existing numerical timeout-reconciliation policy:
compare observed spool usage with the saved baseline and requested length.
Aggregate totals cannot establish request identity when external clients also
change usage. Replacing that inference with explicit ambiguity/recovery behavior
is the remaining implementation change; this refactor does not claim exactly-once
consumption or solve concurrent external writers.
