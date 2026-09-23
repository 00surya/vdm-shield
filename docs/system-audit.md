# System audit — 18 September 2026

## Verified changes

- Training and live clip inference now sample eight frames across the full clip, including its end. Previously, live sampling could omit the final part of an event.
- Source-separated evaluation now includes held-out event labels absent from a training fold. Previously these examples were excluded, which could overstate performance.
- Evaluation reports coverage and per-label correct/incorrect counts. Folds without enough training data remain excluded and their skipped clips are disclosed.
- Events supports text search, review and priority filters, and undo review. Scores identify their origin; they are not measured accuracy.
- Analyze explains the workflow; Training identifies the additional normal/event clips needed and shows per-label validation results.
- The standalone benchmark can run without setting PYTHONPATH.

## Measurements

Local throughput check using repeated bundled sample imagery: 138 processed frames, median pose throughput 10.0 FPS, six depth samples, first pose at 1.82 seconds and first depth at 2.64 seconds. This is a throughput smoke test, not an accuracy benchmark or a guarantee for multiple cameras.

The threat worker loaded successfully and returned no detections on a blank frame; measured inference latency was 426 ms. This checks execution, not gun/knife recall.

Dataset audit: two available raw clips (one normal, one fight), zero reviewed clips, two source IDs, ten records missing raw evidence, one unreviewed model-generated example excluded. No credible accuracy comparison is possible with this data.

## Model decision and remaining limitations

Retained YOLO11n pose, MiDaS Small and the existing threat detector. No representative labeled evaluation establishes that another model is more accurate for this installation. MiDaS provides relative depth, not distance in metres. The clip learner is an experimental centroid classifier; the Train button does not fine-tune pose, depth or object detection.

Before comparing replacement models, independently label continuous footage from several cameras, including incidents the system did not flag. Compare event recall, false alarms per hour, per-class precision, and latency on the same held-out footage. Reviewing only saved detector clips cannot measure missed incidents. Distinct upload IDs do not ensure independently filmed footage; avoid duplicate recordings across validation sources.

63 automated tests passed, including regressions for rare held-out labels and live sampling of the final frame. Restart an existing app process to load Python/template changes.
