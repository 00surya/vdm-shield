# Colab weapon training kit

Upload `notebooks/weapon_training_colab.ipynb` at https://colab.research.google.com/
(File → Upload notebook), choose **Runtime → Change runtime type → T4 GPU**, then
run the cells in order. The notebook contains the Python scripts itself: no GitHub
repository, dataset token, or second upload is required. It installs the standalone
requirements, downloads data and pretrained weights, trains a YOLO26n candidate,
evaluates it and exports ONNX. Optional Google Drive storage is enabled to preserve
checkpoints across Colab resets. Colab availability and session limits vary.

`artifacts/weapon-training-kit.zip` contains the notebook, editable scripts and this
guide for local use. It does **not** contain a newly trained model or dataset images.
Run `python scripts/build_weapon_notebook.py` after editing scripts to refresh both
the notebook's embedded copies and ZIP.

## What the downloader actually provides

The pinned [fcakyon/gun-object-detection dataset](https://huggingface.co/datasets/fcakyon/gun-object-detection)
mirrors [ashish's Roboflow dataset](https://universe.roboflow.com/ashish-cuamw/test-y7rj3).
Its source documentation declares **CC BY 4.0**. The script preserves attribution,
source revision, archive SHA-256 checksums, the declared license and conversion
changes. This records the publisher's declaration, not an independent verification
of every original image's rights.

- Download: two archives, about **92 MB**, containing **4,666 images**.
- Prepared labels: **0 gun** (pistol + rifle), **1 knife**, **2 grenade**.
- One known incomplete sample is excluded: a visible rifle lacked a box beside a
  labeled grenade. With seed 42, this leaves **3,209 train / 551 val / 905 test**.
- Original validation is reserved as test. Validation is a deterministic 15% split
  of original training filename families. Exact decoded duplicates are removed;
  variants sharing the original filename stay together. Original videos/cameras
  are unknown, so near-duplicate and scene leakage can remain.
- All retained source images contain annotated weapons: **there are no negative
  images**. Add reviewed weapon-free CCTV, phones, tools and other lookalikes.
- These are **upstream starter annotations**, not fully reviewed training labels.
  More missing boxes may remain. Import the prepared YOLO images/labels into an
  annotation tool, correct all target boxes, and update reviewed records as you
  review them. The notebook's preview samples all three classes.
- There is **no explosion or generic bomb class**. Images were stretched to 416 ×
  416 upstream; training at 640 cannot recover lost detail.

The scripts deliberately record `reviewed=upstream`, and experiments using these
labels require `--allow-upstream-annotations`. They never mark downloaded labels as
human-reviewed. Passing the audit establishes file consistency, not accuracy.
Keep a separate, reviewed CCTV test set for any deployment decision.

## Local download, prepare and train

Use a Python environment with a matching PyTorch/torchvision pair for your platform.
Colab supplies these already. Install this kit's requirements in that environment:

```sh
python -m pip install -r requirements-weapon-training.txt
python scripts/prepare_weapon_data.py \
  --cache data/weapon_downloads --output data/weapon_starter \
  --weights models/yolo26n.pt
python scripts/train_weapons.py check \
  --data data/weapon_starter/dataset.yaml --allow-upstream-annotations
python scripts/train_weapons.py train \
  --data data/weapon_starter/dataset.yaml --allow-upstream-annotations \
  --weights models/yolo26n.pt --device 0 --imgsz 640 --batch 8 --epochs 100 \
  --output runs/weapons-v1
```

For CPU training use `--device cpu --batch 2`; this is much slower. If CUDA runs out
of memory, reduce batch to 4 or 2. The starting `yolo26n.pt` is COCO-pretrained, **not
a pretrained weapon specialist**. The downloader verifies its official release
checksum. Output directories must be new; nothing is silently overwritten. To
prepare from an existing archive cache without network, pass `--offline`.

For your own annotations, use `train_weapons.py init`, fill its dataset layout and
provenance CSV, and follow [the full training guide](weapon-training.md). That
scaffold defaults to four classes, including explosion; remove or add classes
deliberately and provide examples in every split. Do not mix that class order with
a three-class checkpoint. All images need matching labels, including empty label
files for reviewed negatives. Keep all frames from one video/site together.

## Convert for the deployment device

Start with FP32 ONNX as the portable CPU baseline. All profiles take a trained
`best.pt` and the matching dataset YAML; quantization may change detection quality.

| Target | Script profile | Extra dependencies / execution location |
| --- | --- | --- |
| Windows/Linux/macOS CPU | `onnx-cpu` | Included in base requirements |
| Intel CPU or supported Intel accelerator | `openvino-fp32` | `openvino>=2025.2,<2027` |
| Intel CPU / supported INT8 device | `openvino-int8` | Above plus `nncf>=2.14,<4`; training data for calibration |
| Apple Silicon / Apple deployment | `coreml-fp16` | Run on Mac; `coremltools>=9,<10`, `numpy<=2.3.5` |
| NVIDIA GPU / Jetson | `tensorrt-fp16` | Run on target CUDA device; TensorRT >=8.5 matching CUDA/JetPack |

Install only the extra dependencies for the profile you need, ideally in a separate
conversion environment. For CoreML, for example:
`python -m pip install 'coremltools>=9,<10' 'numpy<=2.3.5'`.
Use NVIDIA's target-specific TensorRT/JetPack installation instructions rather than
installing arbitrary CUDA packages over the Colab or Jetson runtime.

```sh
python scripts/export_weapons.py \
  --weights runs/weapons-v1/fit/weights/best.pt \
  --data data/weapon_starter/dataset.yaml --allow-upstream-annotations \
  --target onnx-cpu --imgsz 640 --output runs/weapons-v1-onnx
```

Substitute the table's profile and a new output directory. TensorRT additionally
needs `--device 0`. INT8 uses **training images only**, including when the exporter
defaults to a validation entry; a separate calibration YAML prevents accidentally
using held-out images. `--calibration-fraction 0.25` uses a subset; ensure it covers
representative scenes and all classes. The default uses the full training split.

Every profile exports a fixed square batch-1 model with YOLO26's NMS-free head.
`candidate.json` records class order, input size, checksums, package versions and
calibration settings. The original `.pt` is preserved alongside the export.
TensorRT engines depend on GPU/runtime versions: rebuild on the intended device.
CoreML conversion and validation are separate Mac steps, not Colab steps. ONNX on
ARM requires a compatible ONNX Runtime build; this kit does not provide Android,
iOS app integration, TFLite, NCNN or vendor-specific NPU packages.

## Measure the exported model

```sh
python scripts/train_weapons.py evaluate \
  --weights runs/weapons-v1-onnx/candidate.onnx \
  --data data/weapon_starter/dataset.yaml --allow-upstream-annotations \
  --split val --imgsz 640 --output runs/weapons-v1-onnx-val
python scripts/benchmark_weapons.py \
  --weights runs/weapons-v1/fit/weights/best.pt runs/weapons-v1-onnx/candidate.onnx \
  --imgsz 640 --device cpu --runs 30 --output runs/latency.json
```

The same evaluation command accepts OpenVINO directories, `.mlpackage` directories
on Mac, or `.engine` files with `--device 0`. Benchmark on each target computer;
runtime thread defaults differ. The benchmark includes the Ultralytics adapter's
pre/postprocessing and inference, not the complete camera/alert pipeline. It is a
latency measurement, not accuracy or supported-camera-count evidence. Use validation
to select settings, then use `--split test` once settings are fixed. Compare per-class
recall/AP before and after conversion. Operational acceptance also needs false alarms
per camera-hour and small/distant-weapon misses on representative CCTV.

No trained accuracy or speed improvement is promised. The project's existing weapon
detector remains active; integrating a candidate, its class mapping and its optimized
runtime is a separate change after validation. Framework/model licensing also applies
to exported weights; check [Ultralytics' licensing options](https://www.ultralytics.com/license)
for the intended product distribution.

## Verification in this workspace

The pinned archives were downloaded and the complete 4,665-image prepared dataset
passed structural checks. A CPU smoke run trained one epoch on **12 real training
images**, exported FP32 ONNX, evaluated it on a separate six-image subset, and ran
the PyTorch/ONNX latency harness. These tiny subsets verify execution only; they do
not establish a useful trained model. Automated tests cover data conversion,
checksums, duplicates, class mappings, export profiles, train-only calibration and
notebook/bundle consistency. The Colab GPU session and native OpenVINO, CoreML and
TensorRT conversions have **not** been executed here; validate those on their targets.

Implementation references: [export API](https://docs.ultralytics.com/modes/export/),
[OpenVINO](https://docs.ultralytics.com/integrations/openvino/),
[CoreML](https://docs.ultralytics.com/integrations/coreml/),
[TensorRT](https://docs.ultralytics.com/integrations/tensorrt/).
