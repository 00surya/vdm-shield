# Saved evidence review — 18 September 2026

Inspected three saved event records and decoded sampled frames from their annotated and raw clips. Two original knife detections are confirmed with operator training label `fight`; the other record is a confirmed heuristic fight. The stored corrections are correct. Original detector categories are retained for traceability.

The newest knife sample places a large knife bounding box over a door/pillar-shaped region. A knife cannot be verified in that sampled frame. This is an apparent false detection requiring operator review, not evidence that weapon accuracy improved. No existing review or label was changed during this audit.

Existing annotated clips are 100 frames at 10 FPS, 640×480. Raw clips are 40 frames at 10 FPS, 320×240. The heuristic fight recording contains a lingering knife alert banner from the shared live display. These historical recordings were preserved.

Changes:
- Headings display the operator label, with the original detector proposal shown separately.
- Future evidence buffers exclude the live alert banner; pose/object overlays remain detector proposals.
- Future raw buffers retain up to 10 seconds at up to 640 pixels wide, bounded by 32 MB, matching the nominal review window. Actual duration may be shorter at startup or under memory pressure.
- New records store actual annotated and raw source ranges, distinct from signal positions. Old records show timing as unavailable rather than inventing a range.
- Mixed-size frames are resized before encoding so injected object frames are not silently dropped by the video writer.

Remaining limits: heuristic clips currently end at the triggering/grouped signal, while object clips collect approximately one second afterward when available. Review clips are sampled/resampled evidence, not lossless original recordings. Separate uploads of the same video can create duplicate training examples. Existing low-resolution clips require reanalysis of the source for improved evidence; this audit did not overwrite them or retrain a model.
