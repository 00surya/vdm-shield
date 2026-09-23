# Lightweight weapon detector training

Start with **YOLO26n**, fine-tuned from local pretrained weights. Its optional
NMS-free head is suitable for a compact ONNX deployment. Compare YOLO26s on the same
data only if nano misses too many small weapons. A GPU is useful for training;
deployment can use CPU. Neither customer-device FPS nor weapon accuracy is guaranteed.

For a downloadable public starter dataset, a self-contained Colab notebook, and
device-specific export commands, start with [the Colab kit](weapon-colab.md).
It covers gun, knife and grenade; its upstream annotations still need review.
This setup also provides dataset checks, training, evaluation and a latency harness.
**There is no production-trained weapon model included.** The current pinned detector remains active. The dashboard's Training
page trains the event classifier, not weapon bounding boxes.

## Dataset preparation

```sh
.venv/bin/python -m pip install -e '.[vision,weapon-training,test]'
.venv/bin/python scripts/train_weapons.py init
```

The scaffold is `data/weapon_dataset/`, ignored by Git:

```text
dataset.yaml
sources.csv
images/train/     labels/train/
images/val/       labels/val/
images/test/      labels/test/
```

The initial names preserve the current detector's intended coverage:

| ID | Class | Annotation meaning |
| --- | --- | --- |
| 0 | gun | Visible handgun or long gun; box the object, not the person |
| 1 | knife | Visible knife, including its blade and handle |
| 2 | grenade | Recognizable visible grenade-like object |
| 3 | explosion | Visible explosion-like region; an event appearance, not a bomb object |

A camera cannot establish whether a gun is real/loaded or a bag contains explosives.
Do not infer a generic `bomb` label from suspicious bags or behavior. There is no
catch-all “any weapon” class. Add machetes, axes or batons as separately defined
classes only with suitable annotations; their runtime alert mappings also need
updating before activation. Document inert replicas and evaluate that ambiguity.

Use CVAT or another annotation tool to export YOLO detection labels. Each image
needs a matching `.txt` file in `labels/<split>/`, with one row per object:

```text
class_id x_center y_center width height
```

Coordinates are normalized to [0,1] using image width/height. Annotate **all target
objects**. An empty label file explicitly means a reviewed negative; missing files
are rejected. Do not add a background class. Fight-clip labels are not box labels.

Add one `sources.csv` row per image:

```csv
image,source_group,origin,usage_rights,reviewed
images/train/entrance_001.jpg,site-a-camera-1,owned recording reference,documented permission reference,yes
```

The rights field records your evidence; the script cannot verify permission itself.
Keep each camera/site group, original video, burst and augmented/re-encoded copies
in one split. Start around 70/15/15 **by source groups**, not neighboring frames.
Validation selects settings; reserve the test set for the final comparison. The
checker finds overlapping groups and identical decoded images. Near-duplicates and
incorrect grouping still need manual review.

Collect realistic CCTV views: distant objects, occlusion, low light, infrared, blur,
compression and diverse backgrounds. Include no-weapon footage and hard negatives
such as phones, tools, umbrellas and reflections. An initial collection budget of
roughly 1,000+ diverse annotated instances per class is a planning target, not an
accuracy threshold. Use learning curves to decide when more data is needed.
Thousands of adjacent frames of one weapon are not thousands of independent examples.
Keep continuous negative videos for measuring false alarms per camera-hour.

Prefer owned or permissioned footage. Public datasets can seed coverage after
checking annotation completeness, domain match and rights. [Open Images V7](https://storage.googleapis.com/openimages/web/download_v7.html)
provides images and bounding boxes for many classes; it is not a complete weapon
dataset. Preserve attribution and check its [license information](https://storage.googleapis.com/openimages/web/factsfigures_v7.html#licenses).
Human-review any model-generated annotations before including them.

```sh
.venv/bin/python scripts/train_weapons.py check --data data/weapon_dataset/dataset.yaml
```

Training rejects empty splits, missing classes/labels, invalid boxes, unreviewed or
missing provenance, duplicates and source leakage. Warnings identify tiny boxes
after resize and low class counts. Passing establishes structure, not label accuracy.

## Train, evaluate and export

`models/yolo26n.pt` already exists here. Other machines need official local weights;
this script does not automatically download models or datasets. Use a **new output
directory** per run. Example on a CUDA training workstation:

```sh
.venv/bin/python scripts/train_weapons.py train \
  --data data/weapon_dataset/dataset.yaml --weights models/yolo26n.pt \
  --device 0 --imgsz 640 --batch 8 --epochs 100 --output tmp/weapons-nano-v1
```

Use `--device cpu --batch 2` for a small plumbing check; CPU training can be slow.
The recipe uses a pretrained backbone, fixed seed, early stopping and late mosaic
shutdown. Begin at 640 pixels: shrinking input can erase a distant knife. Compare
480 only after checking small/distant-object recall. If testing targeted crops,
retain full-frame scans; pose-only crops miss unattended or occluded objects.

```sh
.venv/bin/python scripts/train_weapons.py evaluate \
  --data data/weapon_dataset/dataset.yaml \
  --weights tmp/weapons-nano-v1/fit/weights/best.pt --split val \
  --output tmp/weapons-nano-v1-validation

.venv/bin/python scripts/train_weapons.py export \
  --data data/weapon_dataset/dataset.yaml \
  --weights tmp/weapons-nano-v1/fit/weights/best.pt \
  --output tmp/weapons-nano-v1-onnx

.venv/bin/python scripts/train_weapons.py evaluate \
  --data data/weapon_dataset/dataset.yaml \
  --weights tmp/weapons-nano-v1-onnx/candidate.onnx --split test \
  --output tmp/weapons-nano-v1-onnx-test
```

Reports record per-class metrics, dataset fingerprint, checkpoint checksum, settings
and export checksum. Class order must match the dataset; an older checkpoint with a
different order needs an appropriately remapped annotation copy before comparison.
Export uses static square batch-1 FP32 ONNX with the NMS-free head. Re-evaluate the
exported artifact; successful conversion is not proof of equivalence or accuracy.

```sh
.venv/bin/python scripts/benchmark_weapons.py \
  --weights models/threat-yolov8n.pt models/yolo26n.pt \
  --imgsz 480 640 --threads 1 --output tmp/weapon-cpu-benchmark.json
```

This measures PyTorch CPU adapters on one identical image at fixed square sizes,
including preprocessing/postprocessing, three warmups and twenty timed runs.
COCO-pretrained YOLO26n is not yet a trained weapon detector. Benchmark exported
runtimes and the complete multi-camera pipeline separately on target hardware.

Development baseline, 2026-09-22, Apple M5/macOS arm64, one CPU thread and ten timed
runs on bundled `bus.jpg` (median adapter time):

| Model | 480 × 480 | 640 × 640 |
| --- | ---: | ---: |
| Existing YOLOv8n weapon checkpoint, 4 classes | 35.04 ms | 52.79 ms |
| COCO YOLO26n checkpoint, 80 classes | 36.85 ms | 60.23 ms |

YOLO26n did not beat the current detector in this PyTorch check. These different
class heads and training tasks do not establish a final weapon-model comparison.
Keep the existing model as a baseline; compare the trained candidate and exported
runtime before claiming a speed or accuracy gain. The 320-pixel, one-epoch generated
fixture used to smoke-test train → export → evaluate is software verification only.

## Deployment and acceptance

- First deployment candidate: ONNX Runtime CPU.
- On supported Intel hardware, test OpenVINO INT8 calibrated using representative
  **training-partition** images. Re-evaluate small-weapon recall after quantization.
- On Apple Silicon, measure CoreML; on NVIDIA hardware, measure TensorRT FP16.
- If nano's recall is inadequate, compare a small model or experiment with a larger
  offline teacher and distilled nano student. OpenVINO INT8 is available through
  `export_weapons.py`; distillation is not implemented.

Measure per-class PR/AP, small/distant-object misses, event-level recall, false alarms
per camera-hour and capture-to-alert p50/p95 under simultaneous load. The built-in
evaluation supplies detection metrics, not operational event metrics. Select
per-class alert thresholds on validation PR curves, then lock them for the final
test. A 0.9 model score does not imply 90% real-world reliability.

The current worker runs YOLO26s general objects before publishing weapon results
and samples up to 2 FPS. Faster inference alone cannot remove that scheduling delay.
Later integration should prioritize weapon results and measure the optional general
context cost while retaining full-frame safety scans in Eco mode.

New checkpoints are **candidates**, not automatically installed: `ThreatModel` still
accepts only its pinned checksum and known classes. Runtime integration, alert
threshold calibration, packaging and target-device acceptance follow evaluation.

For proprietary sales, account for framework/model licensing as well as image rights.
Ultralytics offers AGPL-3.0 and Enterprise licensing; its [published terms](https://www.ultralytics.com/license)
specify Enterprise for proprietary use. Fine-tuning/exporting does not remove those
obligations.

Sources: [YOLO26 and head selection](https://docs.ultralytics.com/models/yolo26/),
[YOLO box format](https://docs.ultralytics.com/datasets/detect/),
[OpenVINO export/calibration](https://docs.ultralytics.com/integrations/openvino/).
