import base64
import os
from importlib.metadata import version
import time
from pathlib import Path
from urllib.parse import urlsplit
import uuid

from flask import Blueprint, current_app, jsonify, render_template, request, send_file
from werkzeug.utils import secure_filename

from ..models.sources import SourceSettings
from ..capacity import capacity_report, estimate_capacity
from ..hardware_planner import plan_hardware


web = Blueprint("web", __name__)
VIDEO_EXTENSIONS = {".mp4", ".avi", ".mov", ".mkv", ".webm", ".m4v"}
TRAIN_LABELS = {"normal", "fight", "possible_fall", "person_down", "person_down_after_fight", "hands_up",
                "robbery", "chain_snatching", "crowd_gathering", "fainting", "hit_and_run", "accident", "other_anomaly",
                "gun_detected", "knife_detected", "grenade_detected", "possible_explosion"}


def manager():
    return current_app.extensions["vmd_manager"]


def error(message, status=400):
    return jsonify(error=message), status


@web.get("/")
def index():
    return render_template("index.html", internal_tools=os.getenv("VMD_INTERNAL_TOOLS") == "1")


@web.get("/api/sources")
def sources():
    return jsonify(sources=manager().list(), limit=manager().limit)


@web.get("/api/capacity")
def capacity():
    report = capacity_report(manager().list(), manager().data_dir)
    device = report["device"]
    device["evidence"] = manager().store.evidence_health()
    device["product"] = {
        "name": os.getenv("VMD_DEVICE_NAME", "VMD Shield"),
        "version": version("vmd-shield"),
        "support": os.getenv("VMD_SUPPORT_CONTACT", "Support contact not configured"),
        "update_status": "Managed updates are not configured",
        "camera_limit": "Hardware camera limit not validated",
        "access": "Password protected" if current_app.config.get("VMD_ACCESS_HASH") else "Not provisioned",
    }
    if manager().configuration_error:
        device["issues"].append(manager().configuration_error)
    if device["evidence"]["last_error"] or not device["evidence"]["writer_alive"]:
        device["issues"].append("Evidence saving needs attention. Review the evidence health panel.")
    if device["issues"]:
        device["status"] = "attention"
    return jsonify(report)


@web.post("/api/capacity/estimate")
def capacity_estimate():
    try:
        return jsonify(estimate_capacity(request.get_json(silent=True) or {}))
    except ValueError as exc:
        return error(str(exc))


@web.post("/api/capacity/plan")
def capacity_plan():
    try:
        report = capacity_report(manager().list(), manager().data_dir)
        return jsonify(plan_hardware(request.get_json(silent=True) or {}, report["baseline_fps"],
                                     report["disk_free_gb"], report["hardware"], report["baseline_source"]))
    except ValueError as exc:
        return error(str(exc))


@web.route("/api/evidence-retention", methods=["GET", "PUT"])
def evidence_retention():
    store = manager().store
    if request.method == "GET":
        return jsonify(days=store.evidence_retention_days, applies_to="new_evidence_only")
    try:
        days = store.set_evidence_retention_days((request.get_json(silent=True) or {}).get("days"))
    except ValueError as exc:
        return error(str(exc))
    return jsonify(days=days, applies_to="new_evidence_only")


@web.post("/api/sources")
def add_source():
    form = request.form
    kind = form.get("kind", "")
    upload_path = None
    try:
        if set(form).intersection({"model", "depth", "device", "depth_fps", "pose_model"}):
            return error("Model settings are fixed for this app.")
        if len(manager().list()) >= manager().limit:
            return error("Remove a source before adding another (maximum four).", 409)
        if kind == "upload":
            file = request.files.get("video")
            if not file or not file.filename:
                return error("Choose a video file.")
            extension = Path(file.filename).suffix.lower()
            if extension not in VIDEO_EXTENSIONS:
                return error("Use MP4, AVI, MOV, MKV, WebM, or M4V video.")
            upload_path = manager().uploads / (uuid.uuid4().hex + extension)
            file.save(upload_path)
            source_value = str(upload_path)
            name = form.get("name", "").strip() or secure_filename(Path(file.filename).stem)[:64] or "Uploaded video"
        elif kind == "camera":
            source_value = form.get("source", "").strip()
            parsed = urlsplit(source_value)
            if not source_value.isdecimal() and parsed.scheme not in {"http", "https", "rtsp", "rtsps"}:
                return error("Enter a webcam index or HTTP/RTSP camera stream URL.")
            name = form.get("name", "").strip() or "Camera"
        else:
            return error("Choose a video upload or camera input.")
        if upload_path:
            from .library import remember_upload
            remember_upload(upload_path, file.filename[:200])
        eco_mode = kind == "camera" and form.get("eco_mode") == "on"
        settings = SourceSettings(source=source_value, name=name[:64], kind=kind, eco_mode=eco_mode)
        return jsonify(manager().add(settings)), 201
    except (ValueError, RuntimeError, OSError) as exc:
        if upload_path:
            upload_path.unlink(missing_ok=True)
        return error("Could not save camera configuration." if isinstance(exc, OSError) else str(exc), 409 if "maximum" in str(exc) else 400)


@web.get("/api/sources/<camera_id>/frames")
def frames(camera_id):
    engine = manager().get(camera_id)
    if engine is None:
        return error("Source not found.", 404)
    with engine.lock:
        values, state = dict(engine.frames), dict(engine.state)
    meta = engine.depth_metadata(state)
    if meta["stale"] or meta["status"] == "error":
        values["depth"] = None
    try:
        pose_after = int(request.args.get("pose_after", "-1"))
        depth_after = int(request.args.get("depth_after", "-1"))
        threat_after = int(request.args.get("threat_after", "-1"))
    except ValueError:
        return error("Frame sequence must be an integer.")
    pose_sequence = state.get("sequence", 0)
    depth_sequence = meta["sequence"]
    threat_stale = engine.snapshot()["threat_stale"]
    threat_sequence = state.get("threat_sequence")
    return jsonify(frame_identity=state.get("frame_identity"), sequence=pose_sequence, depth_meta=meta,
                   threat_sequence=threat_sequence, threat_status=state.get("threat_status", "unavailable"),
                   threat_stale=threat_stale,
                   threat=base64.b64encode(values["threat"]).decode() if values.get("threat") and not threat_stale and state.get("threat_status") == "ready" and threat_sequence != threat_after else None,
                   pose=base64.b64encode(values["pose"]).decode() if values.get("pose") and pose_sequence != pose_after else None,
                   depth=base64.b64encode(values["depth"]).decode() if values.get("depth") and depth_sequence != depth_after else None)


@web.post("/api/sources/<camera_id>/<action>")
def control(camera_id, action):
    engine = manager().get(camera_id)
    if engine is None:
        return error("Source not found.", 404)
    try:
        if action == "stop":
            manager().set_enabled(camera_id, False)
        elif action == "restart":
            manager().set_enabled(camera_id, True)
        elif action == "remove":
            manager().remove(camera_id)
            return jsonify(removed=camera_id)
        else:
            return error("Unknown action.", 404)
    except (RuntimeError, OSError) as exc:
        return error("Camera control could not be saved." if isinstance(exc, OSError) else str(exc), 409)
    return jsonify(engine.snapshot())


@web.get("/api/incidents")
def incidents():
    return jsonify([item for item in manager().store.incidents() if item["mode"] == "live"])


@web.get("/api/analytics")
def analytics():
    period = request.args.get("range", "24h")
    if period not in {"24h", "7d"}:
        return error("Choose a 24h or 7d range.")
    try:
        timezone_offset = int(request.args.get("tz_offset", "0"))
    except ValueError:
        return error("Invalid timezone offset.")
    if not -840 <= timezone_offset <= 840:
        return error("Invalid timezone offset.")
    bucket_seconds = 3600 if period == "24h" else 86400
    since = time.time() - (24 if period == "24h" else 168) * 3600
    return jsonify(pipeline=manager().list(), evidence=manager().store.evidence_health(),
                   range=period, since=since, bucket_seconds=bucket_seconds,
                   **manager().store.dashboard_metrics(
                       since, bucket_seconds, heatmap_since=time.time() - 7 * 86400,
                       timezone_offset_minutes=timezone_offset))


@web.post("/api/incidents/<incident_id>/review")
def review(incident_id):
    decision = (request.get_json(silent=True) or {}).get("decision")
    if decision not in {"confirmed", "false_positive", "unreviewed"}:
        return error("Invalid review decision.")
    if not manager().store.review(incident_id, decision):
        return error("Incident not found.", 404)
    return jsonify(review=decision)


@web.post("/api/incidents/<incident_id>/label")
def label_incident(incident_id):
    label = (request.get_json(silent=True) or {}).get("label")
    if label not in TRAIN_LABELS:
        return error("Choose a supported training label.")
    if not manager().store.set_training_label(incident_id, label):
        return error("Incident not found.", 404)
    return jsonify(train_label=label, review="false_positive" if label == "normal" else "confirmed")


@web.get("/api/training")
def training_status():
    store = manager().store
    return jsonify(model=store.learner.snapshot(), counts=store.training_counts(),
                   audit=store.training_audit(), normal_samples=store.normal_samples())


@web.post("/api/training/train")
def train_model():
    if not manager().store.learner.start_training():
        return error("Training is already running.", 409)
    return jsonify(manager().store.learner.snapshot()), 202


@web.post("/api/training/normal/<sample_id>/review")
def review_normal(sample_id):
    decision = (request.get_json(silent=True) or {}).get("decision")
    if decision not in {"accepted", "rejected", "unreviewed"}:
        return error("Invalid review decision.")
    if not manager().store.review_normal(sample_id, decision):
        return error("Sample not found.", 404)
    return jsonify(review=decision)


@web.get("/api/training/normal/<sample_id>/clip")
def normal_clip(sample_id):
    sample = next((item for item in manager().store.normal_samples() if item["id"] == sample_id), None)
    if sample is None:
        return error("Sample not found.", 404)
    path = manager().store.raw_clips / sample["raw_clip"]
    return send_file(path, mimetype="video/x-msvideo", as_attachment=True, download_name=path.name)


@web.get("/api/incidents/<incident_id>/clip")
def clip(incident_id):
    item = next((x for x in manager().store.incidents() if x["id"] == incident_id and x["mode"] == "live"), None)
    if not item or not item["clip"]:
        return error("Clip not available.", 404)
    path = manager().store.clips / item["clip"]
    return send_file(path, mimetype="video/x-msvideo", as_attachment=True, download_name=path.name)


@web.get("/api/incidents/<incident_id>/raw")
def raw_clip(incident_id):
    item = next((x for x in manager().store.incidents() if x["id"] == incident_id and x["mode"] == "live"), None)
    if not item or not item["raw_clip"]:
        return error("Raw clip not available.", 404)
    path = manager().store.raw_clips / item["raw_clip"]
    return send_file(path, mimetype="video/x-msvideo", as_attachment=True, download_name=path.name)


@web.route('/api/training/uploads', methods=['GET', 'POST'])
def training_uploads():
    import sqlite3
    import cv2
    store = manager().store
    if request.method == 'GET':
        with store.connect() as conn:
            conn.row_factory = sqlite3.Row
            return jsonify([dict(row) for row in conn.execute('SELECT * FROM training_uploads ORDER BY created DESC')])
    label = request.form.get('label')
    source = request.form.get('group', '').strip()[:100]
    file = request.files.get('video')
    if label not in TRAIN_LABELS or not source or not file or not file.filename or Path(file.filename).suffix.lower() not in VIDEO_EXTENSIONS:
        return error('Choose a video, supported label, and source group (same camera or original recording = same group).')
    item_id = uuid.uuid4().hex
    path = store.raw_clips / (item_id + Path(file.filename).suffix.lower())
    file.save(path)
    cap = cv2.VideoCapture(str(path))
    try:
        ok, _ = cap.read()
        if not ok or cap.get(cv2.CAP_PROP_FRAME_COUNT) < 2:
            path.unlink(missing_ok=True)
            return error('Choose a decodable video clip with at least two frames.')
    finally:
        cap.release()
    with store.connect() as conn:
        conn.execute('INSERT INTO training_uploads VALUES (?,?,?,?,?,?)',
                     (item_id, file.filename[:200], label, 'dataset:' + source.casefold(), path.name, time.time()))
    return jsonify(id=item_id), 201


@web.route('/api/training/uploads/<item_id>', methods=['GET', 'DELETE'])
def training_upload_file(item_id):
    store = manager().store
    with store.connect() as conn:
        row = conn.execute('SELECT raw_clip FROM training_uploads WHERE id=?', (item_id,)).fetchone()
    if not row:
        return error('Training clip not found.', 404)
    path = store.raw_clips / row[0]
    if request.method == 'GET':
        if not path.is_file():
            return error('Training file missing.', 404)
        return send_file(path, as_attachment=True)
    if store.learner.snapshot()['state'] == 'training':
        return error('Wait for training to finish before deleting a training clip.', 409)
    path.unlink(missing_ok=True)
    with store.connect() as conn:
        conn.execute('DELETE FROM training_uploads WHERE id=?', (item_id,))
    return jsonify(deleted=item_id)


@web.post("/api/deployment-plan")
def internal_deployment_plan():
    if os.getenv("VMD_INTERNAL_TOOLS") != "1":
        return error("Internal tools are disabled on this device.", 404)
    import json
    from ..deployment import deployment_plan
    path = manager().data_dir / "validated_devices.json"
    try:
        profiles = json.loads(path.read_text()) if path.exists() else []
        if not isinstance(profiles, list):
            raise ValueError("Device profiles must be a list.")
        return jsonify(deployment_plan(request.get_json(silent=True) or {}, profiles))
    except (ValueError, TypeError, KeyError, OverflowError, OSError):
        return error("Check the sizing inputs and validated device profile configuration.")


@web.put("/api/sources/<camera_id>/fps")
def camera_fps(camera_id):
    try:
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return error("Provide an analysis FPS setting.")
        value = manager().set_target_fps(camera_id, body.get("target_fps"))
        return jsonify(target_fps=value)
    except KeyError:
        return error("Camera not found.", 404)
    except ValueError as exc:
        return error(str(exc))
    except OSError:
        return error("Could not save the FPS setting. No change was applied.", 503)


@web.put("/api/sources/<camera_id>/eco")
def camera_eco_mode(camera_id):
    try:
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return error("Choose whether Eco mode is on or off.")
        enabled = manager().set_eco_mode(camera_id, body.get("enabled"))
        state = manager().get(camera_id).snapshot()
        return jsonify(eco_mode=enabled, eco_state=state.get("eco_state", "starting" if enabled else "off"),
                       camera_id=camera_id)
    except KeyError:
        return error("Camera not found.", 404)
    except ValueError as exc:
        return error(str(exc))
    except OSError:
        return error("Could not save Eco mode. No change was applied.", 503)


@web.get('/api/logs')
def system_logs():
    level = request.args.get('level', 'all')
    if level not in {'all', 'info', 'warning'}:
        return error('Choose all, info or warning.')
    summary = manager().health_log.observe(manager().list(), manager().store.evidence_health())
    return jsonify(summary=summary, entries=manager().health_log.read(level))
