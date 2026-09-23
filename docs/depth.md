# Relative Z with ZipDepth

The application uses [ZipDepth](https://github.com/fabiotosi92/ZipDepth), revision
`91f3fd21e131641f51e8d35736d1958350180e3a`, with `zipdepth_base_npu.pth`.
`scripts/download_depth_model.py` pins the source and checkpoint SHA-256 hashes,
loads weights strictly, and exports an ONNX graph with dynamic spatial dimensions.
It checks numerical agreement against the original model at landscape, portrait,
square and wide input shapes before installing the assets. The upstream MIT notice
is included as `models/ZipDepth-LICENSE.txt`.

The runtime uses one ONNX Runtime CPU thread per source. This is a portable baseline
for machines without dedicated GPUs; it is not a guarantee of multi-camera capacity.
The depth process has a bounded queue and never blocks pose/motion analysis. Normal
sampling targets 1 FPS; quiet Eco mode retains its 0.1 FPS depth heartbeat. Events
can request a priority sample. GPU acceleration has not been enabled or validated.

Inputs are RGB floats in [0,1]. Aspect ratio is preserved while targeting a 256-pixel
short side and limiting the long side to 448 pixels. Replicated padding makes both
dimensions multiples of 32. Output padding is cropped before resizing to the source
frame so torso samples retain their source coordinates. The custom exporter keeps
the model's learned global attention; the upstream static exporter replaces it
with average pooling. The only pooling substitution here is mathematically
equivalent at the supported padded sizes and is checked against the original.

ZipDepth predicts affine-invariant inverse depth, not metric distance. Each map is
normalized using its 2nd and 98th percentiles. A person's inverse-depth estimate is
the median of confident torso patches, rejected when there are fewer than three
usable joints or their median absolute deviation exceeds 0.12. The displayed
`relative_z = 1 - inverse_depth` therefore increases away from the camera.
Flat maps and unreliable poses produce no numeric Z. These quality gates are
heuristics, not calibrated confidence scores.

The API exposes `depth_people: [{track_id, relative_z}]` with `null` for uncertain
values. `depth_meta` identifies the sampled frame, time, capture identity, backend,
and `z_units: relative_0_1`. Routine depth is submitted from frames that received
pose inference, and results are matched by frame sequence, timestamp and shape.
Sampling uses raw keypoint coordinates with the stabilized pose's quality gate,
avoiding lag from drawing smoothing. Missing matches clear previous Z values;
stale/error readings are hidden. Final upload samples use the same matching logic.

The scale changes with scene content: compare people in one sampled frame only.
It cannot supply metres, real-world XYZ coordinates, cross-camera distances or
movement speed. The displayed Z remains advisory. Fight alerts now additionally
require compatible same-frame depth observations across a confirmation window;
depth values are never copied onto newer poses or added to the rule score.

The same two track IDs must have overlapping boxes (intersection at least 5% of
the smaller box), reliable poses, fast camera-compensated motion inside both person
boxes, and the existing image-supported limb/contact evidence. These gates create
a provisional “Checking interaction” state, not an incident. At least five seconds
of supported observations are required before a “Possible fight / review” alert.
Brief motion pauses up to 0.25 seconds retain the track lock but add no evidence
time; longer pauses, missing tracks, unreliable poses, non-overlap or frame gaps
over 0.65 seconds restart it. Existing saved settings cannot shorten the five-second
minimum. Other event types retain their own confirmation rules.

For each sampled frame, relative torso-depth difference plus twice the combined
torso spread must be at most 0.12 to support the pair. Flat, missing, mismatched,
noisy or clearly separated depth cannot confirm a fight. At least three distinct
depth samples must span five seconds, with no sample gap or source-time age over
2.5 seconds. New depth support starts a new motion-evidence window; contradictory
or expired depth clears its accumulated evidence. Pair loss/reacquisition cannot
reuse a sample from before the new interaction. Pending interactions keep Eco mode
active, while the depth worker remains asynchronous. Learned fight predictions
cannot independently bypass these gates.

Box overlap, relative Z and five seconds of motion do not prove contact, violence
or intent. This conservative policy can miss brief fights, strikes without box
overlap, a stationary victim, or events with occlusion/poor depth. All thresholds
need validation against labelled customer scenes, including waving, dancing,
hugging and foreground/background overlap, before making accuracy claims.

## Reproduce checks

```sh
python -m pip install -e '.[vision,depth-export,test]'
python scripts/download_depth_model.py
python -m scripts.smoke_vision
python scripts/benchmark_depth.py --output tmp/depth-benchmark.json
python -m scripts.benchmark_pipeline --seconds 12
python -m pytest -q
```

An optional `--compare-midas` benchmark uses previously installed local MiDaS and
EfficientNet sources/weights plus `timm==0.6.13` and `einops`. It measures both
adapters on identical resized images with one CPU thread, two warmups, and ten
timed runs by default. Resize, inference and normalization are included; model
loading is reported separately. This measures depth latency, not detection accuracy
or customer hardware capacity. Validate near/far ordering, occlusion and lighting
on representative videos before making accuracy claims.

On the development Mac (macOS 26.6.2, arm64), the 2026-09-22 comparison measured
these median adapter latencies with the settings above:

| Resized source image | MiDaS Small | ZipDepth ONNX |
| --- | ---: | ---: |
| 384 × 512 | 127.40 ms | 36.32 ms |
| 960 × 540 | 94.91 ms | 41.43 ms |

This is approximately 2.3–3.5× faster for depth on these samples. It is not a
measurement of improved depth accuracy or of whole-application speed. A separate
full-pipeline run also produced tracked-person Z readings through the asynchronous
worker. Windows, Intel Mac, Linux and multi-camera throughput remain unmeasured.
