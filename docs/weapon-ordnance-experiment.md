# Public weapon and ordnance experiment

This experiment trained a **candidate YOLO26n detector for 30 epochs, followed by 20 additional fine-tuning epochs** on the local Apple GPU. Each phase evaluates its best validation checkpoint on held-out test images, exports ONNX, and evaluates that exact export. It does not activate the model in VDM.

Completed 30-epoch run: `output/weapon-ordnance-30ep-20260923-retry1/`.

Completed 20-epoch extension (50 total): `output/weapon-ordnance-50ep-20260923/`. All training, test evaluation, ONNX export and ONNX test evaluation finished on 23 September 2026 at 06:46 UTC. Its status and log are `status.json` and `train.log` in that directory. See the continuation section below.

On the same 1,434 test images, mAP@50 increased from 70.04% to 71.35%, mAP@50–95 from 48.97% to 50.32%, and precision from 69.35% to 72.10%. Recall was essentially unchanged (65.89% to 65.84%). Improvements were not uniform: `other_ordnance` mAP@50 and recall decreased. The ONNX artifact's SHA-256 matches its manifest and its aggregate metrics agree with PyTorch within 0.000002. The model remains a candidate; it has not been integrated into the live application's weapon detector.

The initial run stopped before training because one HTTP range transfer repeatedly ended early. Its logs remain in `output/weapon-ordnance-30ep-20260923/`. The downloader now resumes within an incomplete part, uses smaller HTTP requests, and preserves previously received bytes. The recovery run reuses the downloaded parts and verifies the same pinned archive checksum before preparation.

## What it learns

| Label | Public annotations mapped to this label |
| --- | --- |
| gun | Pistol and rifle |
| knife | Knife |
| grenade | Grenade from both sources |
| bomb | Mortar bomb, aviation bomb, antisubmarine bomb |
| other_ordnance | Projectile, RPG, rocket, landmine, sea mine, cartridge, cartridge magazine, fuse |

These are visible-object categories. In particular, `other_ordnance` includes components that are not themselves explosives. A regular camera cannot establish chemical composition, concealed contents, whether an item is live, or whether it is a replica. This is not a generic IED or explosive-material detector.

## Sources and preparation

- [fcakyon / gun-object-detection](https://huggingface.co/datasets/fcakyon/gun-object-detection), a CC BY 4.0 mirror of Ashish / Roboflow's dataset. Pinned revision and archive checksums are in `scripts/prepare_weapon_data.py`. Existing prepared set: 4,665 images, with 3,209 train, 551 validation and 905 test images.
- [CTX-UXO / Politehnica Bucharest](https://huggingface.co/datasets/UXO-Politehnica-Bucharest/Contextual_Vision_for_Unexploded_Ordnances), CC BY 4.0, 3,520 published RGB images with bounding-box annotations. Pinned revision `c1e7020b3a0192ab98861f1f07f0119a8b59c8d4`; the archive is 3.90 GB. See [dataset DOI](https://doi.org/10.21227/cwnm-de53).

`scripts/prepare_weapon_ordnance_data.py` downloads the pinned archive using resumable byte ranges, verifies its full SHA-256, remaps **all** annotations, preserves the existing partitions, and removes exact decoded duplicates with held-out partitions taking priority. Malformed boxes are excluded and recorded, not treated as negative scenes. CTX images retain their aspect ratio and are reduced to at most 960 pixels on their longest side. No dataset code or archived pickle is executed.

Actual retained counts, exclusions, annotation provenance and class mapping are written to `data/weapon_ordnance/preparation.json`, `sources.csv`, `ATTRIBUTION.md` and `dataset-audit.json`. Labels are marked `upstream`, not human-reviewed. Original scene IDs are unavailable, so near-duplicate or same-scene leakage can remain. Close-up ordnance photographs are not representative of everyday indoor CCTV; dedicated negative scenes and site testing are still needed.

The completed combined dataset audit passed with **8,185 images**: **5,670 training**, **1,081 validation**, and **1,434 test**. Every class is present in each split. The audit also flags small objects and the lack of reviewed negative scenes. Training uses a fixed seed; PyTorch reports that some MPS operations are not deterministic, so bit-for-bit reproducibility is not guaranteed on the Apple GPU.

## Reproduce

The three-class starter must already exist; use `scripts/prepare_weapon_data.py` first if needed. Use a new output directory for each run:

```bash
.venv/bin/python scripts/run_weapon_experiment.py \
  --prepare-public \
  --data data/weapon_ordnance/dataset.yaml \
  --output output/weapon-ordnance-30ep-new \
  --epochs 30 --imgsz 512 --batch 8 --device mps --threads 4
```

For an already prepared dataset, omit `--prepare-public`. Use `--device 0` on a CUDA host or `--device cpu` without a GPU. Early stopping is disabled with `patience=0`. `last.pt` is saved each epoch, `best.pt` tracks validation fitness, and additional epoch checkpoints are kept every five epochs. The wrapper verifies that the results CSV actually contains epochs 1 through 30 before proceeding to evaluation.

## Monitor and outputs

```bash
cat output/weapon-ordnance-30ep-20260923-retry1/status.json
tail -f output/weapon-ordnance-30ep-20260923-retry1/prepare.log
# Once training starts:
tail -f output/weapon-ordnance-30ep-20260923-retry1/train.log
```

`status.json` records the current stage and process IDs. During training, `training/fit/results.csv` records completed epochs and validation metrics. `status=completed` is written only after training, test evaluation, export and ONNX test evaluation all succeed. On a handled failure it records the error; if the machine shuts down, check whether its process is still alive rather than relying on an old status file.

Expected artifacts after completion:

- `training/fit/weights/best.pt` and `last.pt`
- `training/run.json` and `training/dataset-audit.json`
- `test-pytorch/metrics.json` with aggregate and per-class held-out results
- `onnx/candidate.onnx` and its checksum/class manifest
- `test-onnx/metrics.json` for the exported model

The launched job runs independently of this conversation. An idle-sleep guard lasts only while the job runs; closing the laptop or shutting it down can interrupt computation. Its launch information is in `tmp/weapon-ordnance-job.json`.

## Continue the completed model for 20 more epochs

The original run completed all 30 epochs, plus PyTorch and ONNX test evaluation. To extend its final checkpoint by 20 epochs (50 total), use:

```bash
.venv/bin/python scripts/run_weapon_experiment.py \
  --continue-from output/weapon-ordnance-30ep-20260923-retry1 \
  --data data/weapon_ordnance/dataset.yaml \
  --output output/weapon-ordnance-50ep-20260923 \
  --epochs 20 --imgsz 512 --batch 8 --device mps --threads 4
```

The completed checkpoint was stripped of optimizer state by Ultralytics. Continuation therefore loads the final trained weights into a new fine-tuning phase, with fresh AdamW at `lr0=0.0001`, one warmup epoch, final learning-rate factor `0.1`, and mosaic disabled. It is not an exact optimizer/scheduler resume. Dataset hashes and prior epoch history are checked first, and class order is checked before fine-tuning.

The previous model and results are preserved. `lineage.json` records the parent checkpoint checksum and 30 prior epochs. The new training log numbers the additional epochs 1–20; `status.json` includes both `epochs_completed` for this phase and `total_epochs_completed` toward 50. Early stopping remains disabled. The new candidate receives the same test evaluation, ONNX export and export evaluation steps. Reusing this test set does not make it a new independent final evaluation or guarantee improved performance.

High benchmark scores do not establish camera-hour false-alarm rates, reliable small-object detection, or production readiness. Ultralytics model/software licensing is separate from the datasets' CC BY attribution requirements; retain the project's existing licensing review before commercial distribution.
