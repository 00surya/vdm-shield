# Device development notes

These notes cover the device application in more detail than the [project README](../README.md).
For accounts, groups and device activation, see [group workspaces](group-workspaces.md).
The website is hosted on Heroku; [deployment status](heroku-deployment.md) tracks
email configuration and installer distribution separately.

The [product showcase](product-website.md) is available at
http://127.0.0.1:8766/product, with an interactive exploded 3D architecture tour,
interactive local-learning and Eco mode demonstrations, built-in capabilities,
team roles and the macOS onboarding flow. All five architecture layers run on the
device; web administration is shown separately.

A small local Flask interface for analyzing uploaded videos, webcams, and HTTP/RTSP camera streams. The pipeline uses YOLO11 nano pose tracking, optical flow, ZipDepth relative depth through ONNX Runtime, heuristic rules for possible fights, falls/person down, and hands up, plus a YOLOv8n detector for visible guns, knives, grenades, and explosion-like visuals. Alerts require review. Relative depth is advisory and does not establish physical contact.

## Install and run

Use Python 3.10–3.13:

```sh
python3.11 -m venv .venv
.venv/bin/python -m pip install -e '.[vision,depth-export,test]'
.venv/bin/python scripts/download_models.py
.venv/bin/python run.py
```

Open http://127.0.0.1:8765. You can also double-click `start.command` once the environment is installed. The app only binds to loopback.

Choose **Upload video** for MP4, MOV, AVI, MKV, WebM, or M4V. The recording plays through the analysis pipeline at its source speed; the page shows processed pose/motion frames, sampled depth, metrics, alerts, and playback progress while it runs. Choose **Live camera / stream** for webcam index `0`–`9` or an HTTP/MJPEG/RTSP video stream URL. Stream URLs must point to the video endpoint, not the camera settings page. The live view shows frame freshness instead of a finite progress bar. Up to four sources can run simultaneously. Uploads are saved in `data/uploads/`; incidents, clips, and telemetry are kept in `data/`. Removing a source does not delete evidence or uploaded files.

ZipDepth is the fixed depth model, running through ONNX Runtime with one CPU thread per source, without requiring a GPU. Normal depth sampling targets 1 FPS. The **Relative Z axis** shows each tracked person's position on a 0–1 scale: 0 is nearer and 1 is farther. Values compare people within the same sampled frame only; they are not metres, persistent coordinates, or measurements of approach speed. Missing or inconsistent torso detections show “Z uncertain”; stale readings are hidden. YOLO11 nano pose is the fixed pose model. Pose and motion continue while depth is loading or fails.

The setup command downloads checksum-pinned [ZipDepth](https://github.com/fabiotosi92/ZipDepth) sources and weights, exports ONNX, and verifies predictions against the original model at several input shapes. Only the ONNX file, manifest and MIT notice ship in desktop bundles; the export tools are needed at setup/build time. Existing installations can run `python scripts/download_depth_model.py` after installing the `vision,depth-export` extras. See [depth implementation and validation](depth.md). Existing packaged desktop apps require rebuilding to receive this change.

For close tracked pairs, the dashboard compares torso depth values from the same sampled frame. It reports a relative gap and which person appears nearer only when the difference is clear; otherwise z order is uncertain. This is an advisory view and does not establish physical distance, contact, or an alert by itself.

Fight confirmation requires two overlapping tracked person boxes, reliable poses, fast motion inside both person regions, existing limb/contact evidence, and compatible relative-depth observations from matching frames. The same pair must accumulate at least five seconds of supported evidence before a **Possible fight / review** alert. A **Checking interaction** indicator shows the pending pair and progress; tracking loss, geometric disagreement, stale depth or sustained motion breaks restart confirmation. Quiet Eco sampling stays active while checking. See [confirmation thresholds and limitations](depth.md).

Confirmed fight signals from the same area are grouped into one incident, even if tracking IDs change after a new confirmation window. The incident shows its first and latest signal times and the reasons for repeated triggers. Once confirmed, alerts appear with up to ten seconds of lead-up video; the same record gains up to four seconds of following footage when available. Separate event types and distant interactions remain separate. A sustained person-region motion outlier can also create a lower-priority `other_anomaly` alert after camera-local calibration; it does not identify an event type.

When pose and motion raise an event, the same numbered camera frame is sent as a priority sample to the separate depth and object workers. The pose display does not wait for their results. Routine object samples continue so weapon alerts can also start independently. Matching depth samples now gate fight confirmation without being averaged into the rule score. Missing depth prevents a confirmed fight alert, while pose display and other detectors continue.

## Train from saved clips

New incident detections also save short raw clips in `data/raw_clips/`, without pose drawings or alert banners. The app proposes their event labels. During analysis it saves an occasional provisional normal clip when no event is active. In **Train from saved clips**, an operator can correct an incident label, reject a normal sample, and press **Train model**. Training runs in the background and needs at least three normal clips plus two clips of one event type. Old incidents without raw clips are excluded, so process new footage to build training data.

The learned model uses a frozen ImageNet ResNet18 encoder and a trainable 64-unit GRU head. A successful training run saves a candidate; it only becomes active after source-held-out validation passes the prototype promotion gate. Existing heuristic and weapon alerts remain active. Unreviewed model-generated labels never train themselves. Legacy centroid checkpoints are retained on disk but are not activated. See **Advanced → Help** for the multilingual operator guide and English engineering reference.

On macOS, all models run on CPU because concurrent MPS inference has caused native PyTorch crashes. If the page says it cannot reach the analysis server, restart it with `./start.command` or `.venv/bin/python run.py`, then reload the page.

## MVC layout

- `vmd/models/sources.py`: source configuration and source lifecycle model.
- `vmd/controllers/web.py`: Flask routes for uploads, cameras, frames, and incident review.
- `vmd/templates/index.html`: the single view; `vmd/static/` contains its CSS and JavaScript.
- `vmd/engine.py`, `capture.py`, `vision.py`, `depth_worker.py`, `behavior.py`, `heuristics.py`, `storage.py`, and `learning.py`: video processing, evidence, and local training code.
- `vmd/app.py`: Flask application factory; `vmd/__main__.py`: local entry point.

Run tests with `python -m pytest`. The original presentation UI and FastAPI server have been removed.

## Check the fixed pipeline

```sh
.venv/bin/python -m pytest -q
.venv/bin/python -m scripts.smoke_vision
.venv/bin/python -m scripts.benchmark_pipeline
.venv/bin/python scripts/benchmark_depth.py
```

The smoke check runs both models on a bundled still image. The benchmark repeats that image in a recorded video to measure local processing and depth sampling. Pass `--source /path/to/video.mp4 --seconds 15` to benchmark your own recording. The Sources tab separately shows decoded input FPS, completed pose/motion FPS, browser-view FPS while that tab is open, and frames skipped to stay current. Object and depth workers sample at their own rates. These measurements do not establish event detection accuracy; use representative videos and review the saved incidents to assess alerts.

Pose/motion analysis targets 20 FPS by default. On a faster machine, run `VMD_TARGET_FPS=25 ./start.command` to raise the target (accepted range: 1–60 FPS). Measure the analyzed FPS and skipped-frame count after changing it; a higher target only helps when the hardware can finish each frame quickly enough. The browser view polls separately and may display fewer frames than the analyzer completes. A five-second display buffer adds five seconds of alert-view delay and does not increase inference throughput.

### Eco mode

Eco mode is available for live cameras and can be selected during setup or changed from the Live view card. A low-resolution grayscale motion gate keeps full analysis active while the scene changes and for ten seconds afterwards. During a quiet scene, pose runs at a 1 FPS heartbeat, the specialist weapon/general-object worker receives a 1 FPS safety sample, and depth receives a 0.1 FPS heartbeat. Motion immediately restores the configured pose target and normal background sampling.

Eco mode is off by default. Device & settings provides the on/off control and a side-by-side Normal versus Eco pose-work graph. The Normal value is estimated from the same camera's measured full-analysis rate; the Eco value is the actual pose inference count for that Eco session. Savings appear only after enough active and quiet operation exists for a comparison.

Motion never fully disables weapon checks. This limits the risk from a stationary or slowly introduced visible gun or knife, although sampled inference can still miss a brief, small, hidden or poorly lit object. Uploaded videos always receive normal processing. The UI reports model scans avoided as a compute-work proxy; it is not a measurement of watts, energy, carbon emissions or detection quality. Measure wall power and detection performance on the target appliance before making an environmental or accuracy claim.

The **Device status** tab shows connected cameras, observed analysis speed, worker readiness, disk usage and a single clip cleanup setting. Uploads are excluded from the camera count. Lost video, stopped analysis, delayed workers, failed evidence writes and low disk space require attention. Disk usage covers the whole filesystem containing the data directory, not only VMD clips. Camera throughput is not inferred from RAM or an assumed camera count.

Clip cleanup accepts 1–365 days and affects new alert clips and automatic samples only. Existing clips retain their expiry dates. Event records, uploaded videos and operator-uploaded training clips are retained. Continuous recording is not implemented.

### Attached display / appliance launcher

Connect the VMD device to a TV or monitor over HDMI and attach a mouse/keyboard or a compatible touchscreen. Run:

```sh
.venv/bin/python device.py
```

The launcher starts the local server, waits until it is ready, and opens installed Chromium/Chrome in kiosk mode at Device status. Use `--browser /path/to/chromium` or `--port 8766` if needed. Closing the kiosk stops the server. The ordinary `run.py` launcher remains available for development. A normal TV remote is not supported.

For the final Linux appliance image, configure its desktop session to start this command after graphical login, using absolute paths, for example `/opt/vmd/.venv/bin/python /opt/vmd/device.py`. OS login/autostart configuration is device-specific and is not installed by this repository. The device needs Python dependencies, model files, a graphical desktop, and Chromium/Chrome provisioned before delivery. Camera setup uses the source form; enabled cameras are restored after restart and failed streams are retried. Camera auto-discovery is not implemented. This launcher is a prototype, not a completed production appliance image.


Cameras connect via Ethernet/RTSP or USB. HDMI is optional for a local monitor and does not connect the analysis machine to an operator laptop. The dashboard currently binds to localhost; remote browser use needs a secure network deployment. The current macOS pipeline uses CPU, while Linux can select CUDA when available. A GPU purchase and any ARM64 deployment require testing the complete pose, depth, object and clip pipeline at the desired simultaneous-camera load.
# Object alerts

The source pipeline now includes **Subh775/Threat-Detection-YOLOv8n** for visible guns,
knives, grenades, and explosion-like visuals. It runs locally in a separate process,
sampling up to two frames per second. Sources shows its annotated sampled frame,
availability, and confidence scores. A gun, knife, or grenade score of at least 0.55
raises an immediate review alert; explosion-like detections use 0.70. A per-class
10-second cooldown limits repeated records. These thresholds are initial operating
settings, not calibrated guarantees.

Install this additional asset in an existing checkout:

```sh
.venv/bin/python scripts/download_threat_model.py
.venv/bin/python scripts/smoke_threats.py
```

The normal model setup command also installs it. Downloads use a pinned revision
and SHA-256 verification. Missing or failed object detection is shown in Sources;
pose and motion continue. Restart an existing source after installing weights.

Object alerts save evidence and enter the Events review queue. Their labels can be
corrected like other events. They are excluded from clip-model training until
reviewed. Detecting a knife does not establish harmful intent, and this model cannot
confirm whether a firearm is real, loaded, concealed, or being used threateningly.
It does not cover arbitrary dangerous objects. Small, hidden, or blurred objects
and events between sampled frames may be missed.

Model source and provenance: [author model card](https://huggingface.co/Subh775/Threat-Detection-YOLOv8n),
revision `c6d6fa4e6c9bfd4c4fccb46478db23609e5468fb`,
weights SHA-256 `86c43444ae8319d2276dd300edc3e7f7a1137fe7566737f994ca579ad770f6ce`.
The author lists MIT for the model repository; the underlying Ultralytics
framework has its own AGPL-3.0/commercial licensing. The author's evaluation is
not an evaluation of this application's camera footage.

For a custom lightweight weapon detector, see [weapon dataset and training setup](weapon-training.md).
For a self-contained Google Colab notebook, public dataset downloader and device
conversion scripts, use the [Colab training kit](weapon-colab.md) and
[notebook](../notebooks/weapon_training_colab.ipynb).
`scripts/train_weapons.py` creates a dataset scaffold, validates bounding-box annotations
and source-separated splits, trains YOLO26 nano candidates, evaluates them and exports
ONNX. `scripts/benchmark_weapons.py` measures local runtime latency. Training records
annotation provenance and review status; the existing detector stays active until a candidate
has been evaluated and explicitly integrated.


### General object detection (YOLO26s)

The sampled Objects + threats view now runs the official COCO-pretrained YOLO26s alongside the existing four-class weapon detector. All 80 general classes are available (including people, vehicles, bags and everyday objects). Blue boxes and context counts describe the scene; only specialist weapon outputs enter the threat alarm gate. A general-model knife detection does not independently trigger a weapon alarm. People counts still come from the pose pipeline.

Install/update the vision dependencies, then run `.venv/bin/python scripts/download_object_model.py` (also included in `scripts/download_models.py`). The installer verifies the official release SHA-256. General-model loading or inference failure is shown in source details while weapon detection continues. Both models share the bounded 2 FPS sampled worker; the main pose loop remains separate.

Model: https://docs.ultralytics.com/models/yolo26 . Weights: official Ultralytics assets release v8.4.0, `yolo26s.pt`, SHA-256 `646f8bc3fe0a656803d95c294f7852321748cb29d13466a1af8862e2db384a1b`. Ultralytics AGPL-3.0 / enterprise licensing applies.

Run `.venv/bin/python scripts/benchmark_objects.py` for a local inference check. On this Mac, a bundled bus image produced median warmed inference times of 18.3 ms for weapons and 20.0 ms for general objects (10 runs each); the first combined worker sample took 485 ms. This checks execution and speed, not accuracy. No improved surveillance accuracy is claimed without representative labeled video evaluation.


### Event evidence and history

Events combines evidence video, review decisions, frame inspection, downloads and label correction. History contains records saved more than 12 hours ago, including unreviewed ones. The boundary uses the event saved timestamp, not the recording timestamp. Both tabs support local date/time ranges, text, review and priority filters, with 30 records per page. No evidence is deleted when a record moves into History.

Inline playback creates an H.264 MP4 on demand using the bundled `imageio-ffmpeg` dependency. The first play may take a moment. Converted files are cached under `data/playback`; deleting an event's evidence also removes its playback copies. Original annotated/raw AVI downloads and frame inspection remain available in Detection details & downloads.


Review controls: Save label persists the corrected label and marks an event confirmed (or false positive when the label is normal). Confirm preserves an existing non-normal correction. False alarm sets the label to normal. Undo review clears the operator label and returns the event to unreviewed. The UI shows suggested versus saved labels, unsaved changes, save progress and confirmation. The detected event title remains the original model/rule proposal.


## Temporal learner and SIH operator guide

Install the vision dependencies and run `.venv/bin/python scripts/download_clip_model.py`. The official ImageNet ResNet18 weights are verified against SHA-256 `f37072fd47e89c5e827621c5baffa7500819f7896bbacec160b1a16c560e07ec`. Training and inference use CPU. Inference is queued outside the capture loop, with bounded per-source requests and a three-second result freshness limit. A source restart clears its pending result.

- Input: 16 evenly sampled RGB frames, official resize/crop/normalization at 224×224.
- Encoder: 11,176,512 frozen ResNet18 parameters, 512 features per frame.
- Head: single GRU (64 hidden units), dropout 0.2, linear classifier; 110,976 + 65 × classes trainable parameters.
- Training: AdamW, learning rate 0.001, weight decay 0.0001, batch 8, 30 epochs, seed 42. Reviewed examples have weight 3, provisional examples weight 1; class balancing uses training data only.
- Each training run rebuilds the head. Feature caching avoids re-encoding unchanged files. Byte-identical duplicate clips are collapsed and conflicting duplicate labels excluded; near-duplicates remain an operator responsibility.
- Minimum candidate data: 3 distinct normal clips and 2 distinct examples of an event class. Activation additionally needs 20 reviewed examples across 3 sources, with 5 normal and 5 event examples on a completely held-out source and adequate remaining training data.
- Prototype gate: held-out event recall ≥ 0.80 and precision ≥ 0.60. Replacement cannot reduce either compared with the active model on the same test clips; test sources/content previously used to train the active model block replacement. These gates do not certify deployment accuracy. Reserve an independent final dataset to avoid repeated-validation bias.
- Artifacts: `data/temporal_candidate.npz`, `data/temporal_model.npz`, `data/temporal_previous.npz`, `data/temporal_training.json`, `data/feature_cache/`. NPZ models load with pickle disabled. The previous snapshot is retained for recovery; there is no UI rollback button yet.
- Own dataset: Training → Add your own labeled dataset clips. Supply a true label and a stable camera/original-recording group. Include normal scenes and missed incidents, not only detector-selected alerts. Imported labels count as reviewed.
- Help separates the ten-language operator guide from the English engineering reference. Translations are bundled locally, and the selected operator language is remembered. Dashboard control labels remain English.

Verification: `.venv/bin/python scripts/smoke_temporal.py` exercises the real frozen encoder, head training and model reload using bundled imagery. Measured encoder time on this Mac was about 0.35 seconds per 16-frame clip. Synthetic separability is not an accuracy evaluation on surveillance incidents.

Primary implementation references: [Torchvision ResNet18](https://docs.pytorch.org/vision/stable/models/generated/torchvision.models.resnet18.html), [PyTorch GRU](https://docs.pytorch.org/docs/stable/generated/torch.nn.GRU.html).

### Appliance provisioning and recovery

The main navigation is Live view, Alerts, Recordings, and Device & settings. Training, metrics, history, and help are under Advanced; this is navigation grouping, not role-based authorization.

Camera sources are saved atomically in `data/cameras.json` with owner-only file permissions. Stream credentials may be present there: protect the device disk and backups. Cameras enabled when the service stops are restored on restart. An explicit Stop persists across restarts; Remove deletes the saved configuration. Failed/ended streams and video stale for more than 15 seconds are retried with exponential delays capped at five minutes. Uploads are never restored. Model operations that cannot stop remain subject to the engine's shutdown timeout.

Run `.venv/bin/python scripts/provision_access.py` to provision a password; sign in as `operator` after restarting. All pages, APIs, and media require that password once configured. Without provisioning the app remains a local development instance, and Device status says access is not provisioned. Authentication uses the browser's HTTP Basic prompt on the loopback interface; closing the browser session is required to clear cached credentials. This is one operator account, not separate administrator/viewer roles. Do not expose this HTTP server to a network.

Device status reports evidence writer availability, queue depth, dropped records, last successful alert-clip save in this service session, and encoding errors. “Writer ready” does not guarantee every alert has playable evidence; inspect saved clips.

Set `VMD_DEVICE_NAME` and `VMD_SUPPORT_CONTACT` in the launch environment to brand the support panel. Software version comes from installed package metadata. Updates and hardware camera certification are explicitly shown as unconfigured/unvalidated; no automatic update or throughput guarantee is implied.

For Linux desktop provisioning, adapt `deploy/vmd-kiosk.desktop.example` to the installation path and copy it to the appliance user's desktop autostart directory. This starts after graphical login, not before it. Login configuration, signed updates, OS watchdog/restart policy, device identity provisioning, hardware soak tests, and measured detection-quality acceptance criteria must be completed on the actual appliance image before commercial delivery.

### Internal device sizing

Advanced → Internal device sizing matches camera count, required analysis FPS, input resolution and pipeline ID to `data/validated_devices.json`. No measured profiles ship with the app. Missing matches require benchmarking; they do not produce guessed buying recommendations. This tool is disabled on customer installations by default. When enabled internally, it shares the operator account.

The file is a JSON list of objects with: `name`, `specifications` (string), `validated` (boolean), `tested_cameras` (concurrent streams), `minimum_camera_fps` (minimum sustained per-camera rate), `resolution`, `pipeline`, `benchmark_date`, and `report` (benchmark report identifier). Use a distinct pipeline ID for each model/settings/software configuration. Only mark a profile validated after sustained full-pipeline tests with all workers ready, representative scenes, thermals and evidence writing. Multiple-device counts are calculated from the tested concurrent capacity; cross-device management is not implemented.

Enable the sizing page only on internal engineering installations with `VMD_INTERNAL_TOOLS=1`. It is hidden and its API returns 404 by default on customer devices.

### Partial-class learning and decision fusion

The pretrained 80-class COCO object detector, specialist threat detector, pose/depth/motion heuristics, and learned temporal event classifier are distinct components. Reviewing an event clip trains the temporal classifier only; it does not provide the bounding-box annotations needed to fine-tune either object detector.

A dataset containing normal and fight clips can train a normal/fight classifier, but provides no evidence of improved fall or weapon recognition. Pretrained detectors and heuristics remain active for their existing coverage. Replacement temporal models must retain all previously active labels and pass held-out per-class coverage checks (at least five clips per class and 80% recall), in addition to the existing aggregate and source-isolation gates. These small-data thresholds are prototype safeguards, not commercial validation. Keep reviewed replay examples for previously learned classes and reserve independent scenes/cameras for evaluation. Never relabel unobserved classes as normal. Unknown scenes can still be misclassified; a calibrated abstention/out-of-distribution mechanism is not implemented.

Fight alerts use the tracked-pair, depth and five-second confirmation gate. The temporal classifier cannot independently raise fight or fight-aftermath alerts and bypass that gate. For other classes, fusion remains additive: heuristic and threat alerts continue independently; the classifier can propose additional reviewable events subject to freshness, score and cooldown gates. A temporal prediction of normal never cancels a heuristic/weapon alert. General COCO detections remain contextual and do not become weapon alerts. This is not a calibrated ensemble.

For a future calibrated fusion layer, align detections to the same camera/person/time window, retain each signal's provenance, and learn class-specific decision thresholds on held-out reviewed data. Compare heuristics alone, classifier alone and their combination using per-class recall, false alerts per camera-hour and detection delay. Correlated scores must not be averaged as if they were independent probabilities. Deploy only when the combined system improves those measurements; retain a rollback model. More elapsed time or more labels alone does not establish improvement.

### Team and operator documentation

Open Advanced → Help and choose **Operator** or **Engineering**. The operator guide is available offline in English, Hindi, Tamil, Urdu, Kannada, Telugu, Bengali, Marathi, Gujarati and Malayalam. Urdu uses right-to-left layout. Language preference is remembered locally; search works within the selected guide. These are guide translations, not a translation of all application controls. Non-English translations are drafts requiring native-speaker review before customer deployment.

The engineering reference is available in English in Help and in [docs/engineering-guide.md](engineering-guide.md). It covers the MVC architecture, inference, recovery, evidence, class coverage, validation, fusion, sizing, security and deployment limits. The engineering guide is documentation, not a restricted administrator area.

To add a language, supply a UTF-8 JSON file in vmd/static/docs with the same six operator topic IDs, add its option to docs.html and the allowed language list to docs.js, and extend the language tests. Keep actual English UI labels in translated instructions so operators can locate controls. Verify Urdu bidirectional rendering and script/font support on the final appliance.
