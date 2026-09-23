import threading
import time
import uuid
from collections import deque
from dataclasses import replace
from pathlib import Path

import cv2

from .capture import Capture
from .anomaly import MotionOutlier
from .depth_worker import DepthWorker
from .evidence import EvidenceRecorder
from .eco import EcoGate
from .episodes import EpisodeGrouper
from .heuristics import Rules
from .behavior import BehaviorHeuristic, EVENT_LABELS
from .stabilization import PoseStabilizer
from .spatial import compare_people, person_depths, fight_depth_evidence
from .storage import EvidenceBuffer
from .threats import ThreatWorker, ThreatAlerts, WEIGHTS as THREAT_WEIGHTS
from .vision import Motion, PoseModel, annotate, choose_device, depth_view, synthetic_frame

POSE_WEIGHTS = "yolo11n-pose.pt"
DEPTH_FPS = 1
NORMAL_SAMPLE_INTERVAL_SECONDS = 3600


def evidence_frame_720p(frame):
    """Bound saved annotated evidence to 1280x720 without upscaling."""
    height, width = frame.shape[:2]
    ratio = min(1, 1280 / width, 720 / height)
    if ratio == 1:
        return frame
    size = (max(2, int(width * ratio) // 2 * 2),
            max(2, int(height * ratio) // 2 * 2))
    return cv2.resize(frame, size, interpolation=cv2.INTER_AREA)


class Engine:
    def __init__(self, store, model_dir="models", camera_id="camera-1", name="Camera 1"):
        self.camera_id, self.name = camera_id, name
        self.store, self.model_dir = store, Path(model_dir)
        self.lock = threading.Lock()
        self.lifecycle = threading.Lock()
        self.licence_check = lambda: True
        self.stop_event = threading.Event()
        self.thread = None
        self.capture = None
        self.depth_worker = None
        self.threat_worker = None
        self.settings = None
        self.scenario = "calm"
        self.frames = {}
        self.state = {"status": "idle", "mode": None, "message": "Connect a source or explore the synthetic demo", "people": 0, "score": 0, "fps": 0, "latency_ms": 0, "reasons": [], "signals": {}, "sequence": 0}

    def snapshot(self):
        with self.lock:
            state = dict(self.state)
        if self.capture and getattr(self.capture, "file", False):
            duration = getattr(self.capture, "duration_seconds", None)
            state["duration_seconds"] = round(duration, 2) if duration else None
            if duration:
                position = state.get("source_time", 0)
                state["progress"] = 1 if state["status"] == "finished" else round(min(1, max(0, position / duration)), 3)
        state.update(camera_id=self.camera_id, name=self.name, storage_error=self.store.error, dropped_records=self.store.dropped)
        if self.capture and hasattr(self.capture, "stats"):
            state.update(self.capture.stats())
        age = time.time() - state.get("last_frame_at", time.time())
        state["frame_age_seconds"] = round(age, 1)
        state["stale"] = state["status"] == "running" and age > 3
        state["depth_meta"] = self.depth_metadata(state)
        state["depth_available"] = bool(state.get("depth_available") and not state["depth_meta"]["stale"] and state["depth_meta"]["status"] != "error")
        if not state["depth_available"]:
            state["depth_pairs"] = []
            state["depth_people"] = []
        threat_age = time.time() - state.get("threat_submitted_at", 0)
        if state.get("threat_source_time") is not None:
            threat_age = max(threat_age, state.get("source_time", 0) - state["threat_source_time"])
            state["threat_age_seconds"] = round(max(0, threat_age), 1)
        state["threat_stale"] = state.get("status") != "finished" and threat_age > 3
        if state["threat_stale"] or state.get("threat_status") != "ready":
            state["threat_objects"] = []
            state["scene_objects"] = []
        state["pipeline"] = {
            "pose_motion": {"completed_fps": state.get("processed_fps", 0), "target_fps": state.get("target_fps"),
                            "inference_ms": state.get("latency_ms"), "capture_to_result_ms": state.get("capture_to_processed_ms")},
            "depth": self.depth_worker.metrics() if self.depth_worker and hasattr(self.depth_worker, "metrics") else None,
            "objects_weapons": self.threat_worker.metrics() if self.threat_worker and hasattr(self.threat_worker, "metrics") else None,
        }
        return state

    @staticmethod
    def depth_metadata(state):
        submitted = state.get("depth_submitted_at")
        age = max(0, time.time()-submitted) if submitted is not None else None
        if age is not None and state.get("depth_source_time") is not None and state.get("source_time") is not None:
            age = max(age, state["source_time"]-state["depth_source_time"] + max(0, time.time()-state.get("last_frame_at", time.time())))
        limit = max(3, 2/state.get("depth_fps", DEPTH_FPS))
        if state.get("eco_mode") and state.get("eco_state") == "quiet":
            limit = max(limit, EcoGate.QUIET_DEPTH_INTERVAL + 2)
        return {"status": state.get("depth_status", "off"), "sequence": state.get("depth_sequence"),
                "source_time": state.get("depth_source_time"), "age_seconds": round(age, 1) if age is not None else None,
                "stale": state.get("status") != "finished" and age is not None and age > limit, "latency_ms": state.get("depth_latency_ms"),
                "error": state.get("depth_error"), "target_fps": state.get("depth_fps", DEPTH_FPS), "advisory": True,
                "fight_confirmation_support": True,
                "model": state.get("depth_model"), "backend": state.get("depth_backend"),
                "identity": state.get("depth_identity"), "z_units": "relative_0_1", "z_increases": "farther"}

    @staticmethod
    def depth_sample_state(sample, pose_frames):
        """Join only the raw pose coordinates belonging to this depth sample."""
        cached = pose_frames.get(sample["sequence"])
        depth = sample.get("depth")
        matched = (cached is not None and cached["source_time"] == sample["source_time"]
                   and depth is not None and depth.shape == cached["shape"])
        people = cached["people"] if matched else []
        return dict(depth_sequence=sample["sequence"], depth_source_time=sample["source_time"],
                    depth_submitted_at=sample["submitted_at"], depth_latency_ms=sample["latency_ms"],
                    depth_identity=sample.get("identity", {}), depth_model=sample.get("model", "ZipDepth"),
                    depth_backend=sample.get("backend", "ONNX Runtime CPU"),
                    depth_pairs=compare_people(depth, people) if matched else [],
                    depth_people=person_depths(depth, people) if matched else [],
                    fight_depth_pairs=fight_depth_evidence(depth, people) if matched else [],
                    depth_z_status="ready" if matched else "no_pose",
                    depth_pair_sequence=sample["sequence"])

    def start(self, settings):
        self.store.learner.forget_source(self.camera_id)
        with self.lifecycle:
            if self.thread and self.thread.is_alive():
                raise RuntimeError("Stop the current session before connecting another source")
            self.settings = settings
            self.stop_event = threading.Event()
            self.capture = None
            self.depth_worker = None
            self.threat_worker = None
            self.frames = {}
            self.scenario = "calm"
            with self.lock:
                self.state = {"status": "starting", "mode": settings.mode, "source_kind": getattr(settings, "kind", "camera"), "message": "Loading pipeline", "people": 0, "score": 0, "fps": 0, "target_fps": settings.target_fps, "processed_fps": 0, "processed_frames": 0, "skipped_frames": 0, "latency_ms": 0, "sequence": 0, "reasons": [], "signals": {}, "depth_fps": DEPTH_FPS, "depth_status": "loading", "threat_status": "loading", "threat_objects": [], "eco_mode": bool(getattr(settings, "eco_mode", False)), "eco_state": "starting" if getattr(settings, "eco_mode", False) else "off", "eco_motion_score": 0, "eco_skipped_frames": 0, "eco_processed_frames": 0, "eco_active_pose_frames": 0, "eco_quiet_pose_frames": 0, "eco_active_seconds": 0, "eco_quiet_seconds": 0}
            self.thread = threading.Thread(target=self.run, args=(settings,), daemon=True, name="vmd-inference")
            self.thread.start()

    def stop(self):
        with self.lifecycle:
            self.stop_event.set()
            if self.capture:
                self.capture.stop_event.set()
            if self.thread:
                self.thread.join(timeout=7)
            if self.thread and self.thread.is_alive():
                with self.lock:
                    self.state.update(status="stopping", message="Waiting for the current model operation to finish")
            else:
                with self.lock:
                    self.frames = {}
                    self.state.update(status="idle", mode=None, message="Session stopped", fps=0, processed_fps=0, sequence=0, people=0, score=0, depth_available=False, buffer_seconds=0, signals={}, reasons=[], alert=None, depth_status="off", depth_sequence=None, depth_submitted_at=None, depth_error=None)
                    self.state.update(threat_status="off", threat_objects=[], threat_error=None, threat_sequence=None)

    def run(self, settings):
        buffer = EvidenceBuffer()
        raw_buffer = EvidenceBuffer(seconds=10, max_bytes=32 * 1024 * 1024)
        heuristic = BehaviorHeuristic(Rules(threshold=settings.threshold, hold_seconds=settings.hold_seconds))
        motion_outlier = MotionOutlier()
        episodes = EpisodeGrouper()
        motion = Motion()
        stabilizer = PoseStabilizer()
        pose_frames = {}
        last_depth_pair_sequence = None
        alert, alert_until = None, 0
        seq, previous_source, last_telemetry = 0, None, 0
        previous_sequence, processed_frames, skipped_frames = None, 0, 0
        processed_times = deque()
        last_event_at, last_normal_at, last_model_check = float("-inf"), float("-inf"), float("-inf")
        last_learned_alert = {}
        threat_gate = ThreatAlerts()
        threat_alert, threat_until = None, 0
        last_threat_sequence = None
        evidence_recorder = EvidenceRecorder()
        eco_gate = EcoGate()
        eco_skipped_frames = 0
        eco_processed_frames = 0
        eco_active_pose_frames = 0
        eco_quiet_pose_frames = 0
        eco_active_seconds = 0.0
        eco_quiet_seconds = 0.0
        eco_last_timestamp = None

        def finish_evidence(timestamp, final=False):
            for incident_id in evidence_recorder.ready_ids(timestamp, final=final):
                payload = evidence_recorder.payload(incident_id)
                if payload is None or self.store.enqueue("incident_update", payload):
                    evidence_recorder.finish(incident_id)
                    episodes.release(incident_id)

        def poll_threats():
            nonlocal threat_alert, threat_until, last_threat_sequence, last_event_at
            if not self.threat_worker:
                return
            status, sample, error = self.threat_worker.poll()
            with self.lock:
                self.state.update(threat_status=status, threat_error=error)
            if status != "ready" or not sample or sample["sequence"] == last_threat_sequence:
                return
            last_threat_sequence = sample["sequence"]
            source_time = sample["source_time"]
            # Late detections still refer to their original event, never a newer frame.
            if sample.get("identity", {}).get("session_id") and self.capture and sample["identity"]["session_id"] != getattr(self.capture, "session_id", None):
                return
            with self.lock:
                self.frames["threat"] = sample["jpeg"]
                self.state.update(scene_objects=sample.get("scene_objects", []),
                                  object_status=sample.get("object_status", "unavailable"),
                                  object_error=sample.get("object_error"),
                                  threat_objects=sample["detections"], threat_sequence=sample["sequence"],
                                  threat_source_time=source_time, threat_submitted_at=sample["submitted_at"],
                                  threat_latency_ms=sample["latency_ms"])
            if sample["detections"]:
                last_event_at = max(last_event_at, source_time)
            for event in threat_gate.update(sample["detections"], source_time):
                reasons = [f'Object detector: possible {event["label"]} ({event["confidence"]:.0%} model confidence); review required']
                threat_alert = {"event_type": event["event_type"], "label": f'POSSIBLE {event["label"].upper()} DETECTED',
                                "reasons": reasons, "priority": "high", "detector": "object"}
                threat_until = source_time + 5
                record = {"id": uuid.uuid4().hex, "created": time.time(), "mode": "live",
                          "camera_id": self.camera_id, "camera_name": self.name,
                          "event_type": event["event_type"], "score": event["confidence"], "reasons": reasons,
                          "event_count": 1,
                          "signals": {"model_generated": True, "object_detection": True, "priority": "high",
                                      "object_label": event["label"], "box": event["box"], "source_seconds": source_time,
                                      "frame_identity": sample.get("identity", {}), "detected_at": sample.get("completed_at", time.time()),
                                      "capture_to_detection_ms": sample.get("capture_to_result_ms")}}
                # The async detector may finish after later pose frames were processed.
                # Keep those already buffered post-event frames as well.
                evidence = dict(buffer.snapshot())
                evidence[source_time] = sample["jpeg"]
                raw = dict(self.capture.evidence_between(source_time) if self.capture and hasattr(self.capture, "evidence_between") else raw_buffer.snapshot())
                evidence = {stamp: jpeg for stamp, jpeg in evidence.items() if source_time-10 <= stamp <= source_time+4}
                if sample.get("raw_jpeg"):
                    raw[source_time] = sample["raw_jpeg"]
                evidence_frames, raw_frames = sorted(evidence.items()), sorted(raw.items())
                if self.store.enqueue("incident", (record, evidence_frames, raw_frames)):
                    evidence_recorder.begin(record, evidence_frames, raw_frames)
                with self.lock:
                    self.state["alert"] = threat_alert
        started = time.monotonic()
        pose = None
        try:
            if settings.mode == "live":
                device = choose_device("auto")
                pose = PoseModel(str(self.model_dir / POSE_WEIGHTS), device)
                with self.lock:
                    self.state["device"] = device
                if self.stop_event.is_set():
                    return
                if (self.model_dir / THREAT_WEIGHTS).is_file():
                    try:
                        self.threat_worker = ThreatWorker(device, self.model_dir)
                        self.threat_worker.start()
                        deadline = time.monotonic() + 30
                        while not self.stop_event.is_set() and time.monotonic() < deadline:
                            poll_threats()
                            if self.threat_worker.status != "loading":
                                break
                            self.stop_event.wait(.05)
                    except Exception as exc:
                        if self.threat_worker:
                            self.threat_worker.stop()
                        self.threat_worker = None
                        with self.lock:
                            self.state.update(threat_status="error", threat_error=f"Could not start object detector ({type(exc).__name__})")
                else:
                    with self.lock:
                        self.state.update(threat_status="unavailable", threat_error="Run scripts/download_threat_model.py to enable object alerts.")
                if self.stop_event.is_set():
                    return
                self.capture = Capture(settings.source)
                self.capture.start()
                try:
                    self.depth_worker = DepthWorker(device, self.model_dir, fps=DEPTH_FPS)
                    self.depth_worker.start()
                except Exception as exc:
                    if self.depth_worker:
                        self.depth_worker.stop()
                    self.depth_worker = None
                    with self.lock:
                        self.state.update(depth_status="error", depth_error=f"Could not start depth worker ({type(exc).__name__})")
            for worker in (self.depth_worker, self.threat_worker):
                if worker and self.capture and hasattr(self.capture, "identity"):
                    worker.identity_provider = self.capture.identity
            if self.capture and hasattr(self.capture, "on_frame"):
                def dispatch(sequence, source_time, image):
                    if not getattr(settings, "eco_mode", False):
                        if self.threat_worker:
                            self.threat_worker.submit(sequence, source_time, image)
                        return
                    eco_gate.observe(image, source_time)
                    eco = eco_gate.snapshot()
                    with self.lock:
                        self.state.update(eco_mode=True, eco_state=eco["state"],
                                          eco_motion_score=eco["motion_score"],
                                          eco_safety_scans={"pose_fps": eco["quiet_pose_fps"],
                                                            "threat_fps": eco["quiet_threat_fps"],
                                                            "depth_fps": eco["quiet_depth_fps"]})
                    if self.threat_worker and eco_gate.should_run("threat", source_time):
                        self.threat_worker.submit(sequence, source_time, image)
                self.capture.on_frame = dispatch
            while not self.stop_event.is_set():
                if not self.licence_check():
                    self.stop_event.set()
                    break
                cycle = time.monotonic()
                poll_threats()
                depth_jpeg = None
                depth_state = {}
                sampled = None
                eco_enabled = False
                eco_snapshot = None
                if settings.mode == "demo":
                    timestamp = cycle - started
                    frame, people, depth, flow, camera = synthetic_frame(timestamp, self.scenario)
                    seq += 1
                    ok, encoded_depth = cv2.imencode(".jpg", depth_view(depth))
                    if ok:
                        depth_jpeg = encoded_depth.tobytes()
                    depth_state = dict(depth_status="simulated", depth_sequence=seq, depth_source_time=timestamp, depth_submitted_at=time.time(), depth_latency_ms=0)
                else:
                    packet = self.capture.latest(seq)
                    if packet is None:
                        if self.capture.finished:
                            if self.capture.error:
                                raise RuntimeError(self.capture.error)
                            if self.threat_worker and self.threat_worker.status != "error":
                                final = self.capture.latest(-1)
                                if final and last_threat_sequence != final[0]:
                                    self.threat_worker.last_submit = float("-inf")
                                    submitted_final = self.threat_worker.submit(*final)
                                    deadline = time.monotonic() + 3
                                    while not self.stop_event.is_set() and time.monotonic() < deadline:
                                        poll_threats()
                                        if last_threat_sequence == final[0] or self.threat_worker.status == "error":
                                            break
                                        if not submitted_final and self.threat_worker.status == "ready":
                                            self.threat_worker.last_submit = float("-inf")
                                            submitted_final = self.threat_worker.submit(*final)
                                        self.stop_event.wait(.05)
                            final_packet = self.capture.latest(-1)
                            if self.depth_worker and final_packet and self.state.get("depth_sequence") != final_packet[0]:
                                with self.lock:
                                    self.state["message"] = "Finishing final depth frame"
                                deadline = time.monotonic() + 8
                                self.depth_worker.last_submit = float("-inf")
                                submitted_final = self.depth_worker.submit(*final_packet)
                                while not self.stop_event.is_set() and time.monotonic() < deadline:
                                    status, sampled, error = self.depth_worker.poll()
                                    if (sampled and sampled["sequence"] == final_packet[0]
                                            and (not sampled.get("identity", {}).get("session_id")
                                                 or sampled["identity"]["session_id"] == getattr(self.capture, "session_id", None))):
                                        with self.lock:
                                            self.frames["depth"] = sampled["jpeg"]
                                            self.state.update(depth_status=status, depth_error=error,
                                                              **self.depth_sample_state(sampled, pose_frames),
                                                              depth_available=True, depth_final_frame=True)
                                        break
                                    if status == "error":
                                        with self.lock:
                                            self.state.update(depth_status=status, depth_error=error)
                                        break
                                    if status == "ready" and not submitted_final:
                                        self.depth_worker.last_submit = float("-inf")
                                        submitted_final = self.depth_worker.submit(*final_packet)
                                    self.stop_event.wait(.1)
                                else:
                                    with self.lock:
                                        self.state["depth_final_frame"] = False
                                        if not self.frames.get("depth"):
                                            self.state["depth_status"] = "no_sample"
                            elif final_packet and self.state.get("depth_sequence") == final_packet[0]:
                                with self.lock:
                                    self.state["depth_final_frame"] = True
                            finish_evidence(previous_source or 0, final=True)
                            with self.lock:
                                self.state.update(status="finished", message="Video playback complete", fps=0, processed_fps=0)
                            return
                        self.stop_event.wait(.01)
                        continue
                    seq, timestamp, frame = packet
                    frame_identity = self.capture.identity(seq) if hasattr(self.capture, "identity") else {}
                    eco_enabled = bool(getattr(settings, "eco_mode", False))
                    eco_snapshot = eco_gate.snapshot() if eco_enabled else None
                    if eco_enabled:
                        if eco_last_timestamp is not None:
                            delta = min(1.0, max(0.0, timestamp - eco_last_timestamp))
                            if eco_snapshot["state"] == "quiet":
                                eco_quiet_seconds += delta
                            else:
                                eco_active_seconds += delta
                        eco_last_timestamp = timestamp
                    else:
                        eco_last_timestamp = None
                    if eco_enabled and not eco_gate.should_run("pose", timestamp):
                        newly_skipped = max(1, seq - (previous_sequence or seq - 1))
                        skipped_frames += newly_skipped
                        eco_skipped_frames += newly_skipped
                        previous_sequence = seq
                        eco = eco_gate.snapshot()
                        with self.lock:
                            self.state.update(status="running", message="Eco mode · quiet scene · safety scans continue",
                                              sequence=seq, source_time=timestamp, last_frame_at=time.time(),
                                              skipped_frames=skipped_frames, eco_mode=True, eco_state=eco["state"],
                                              eco_motion_score=eco["motion_score"], eco_skipped_frames=eco_skipped_frames)
                        with self.lock:
                            self.state.update(eco_processed_frames=eco_processed_frames,
                                              eco_active_pose_frames=eco_active_pose_frames,
                                              eco_quiet_pose_frames=eco_quiet_pose_frames,
                                              eco_active_seconds=round(eco_active_seconds, 1),
                                              eco_quiet_seconds=round(eco_quiet_seconds, 1))
                        self.stop_event.wait(.01)
                        continue
                    people = pose.infer(frame)
                    if self.depth_worker:
                        status, sampled, error = self.depth_worker.poll()
                        depth_state = dict(depth_status=status, depth_error=error)
                        if sampled and sampled.get("identity", {}).get("session_id") and sampled["identity"]["session_id"] != getattr(self.capture, "session_id", None):
                            sampled = None
                        if sampled:
                            depth_jpeg = sampled["jpeg"]
                    flow, camera = motion.infer(frame, people, timestamp)
                if settings.mode == "live":
                    raw_people = people
                    for person in people:
                        person.local_flow = motion.person_flow(person)
                    people = stabilizer.update(people, timestamp, motion.joint_motion)
                else:
                    # Synthetic motion evidence exercises the workflow, not the model.
                    for person in people:
                        person.local_flow = flow
                    people = stabilizer.update(people, timestamp, lambda p, i: 2 if self.scenario == "interaction" else 0)
                if settings.mode == "live":
                    # Stabilized drawing coordinates can lag a moving torso. Sample
                    # depth at its raw detections, retaining the pose quality gate.
                    pose_frames[seq] = {"source_time": timestamp, "shape": frame.shape[:2],
                                        "people": [replace(raw, keypoints=list(raw.keypoints), pose_reliable=stable.pose_reliable)
                                                   for raw, stable in zip(raw_people, people)]}
                    if len(pose_frames) > 120:
                        pose_frames.pop(next(iter(pose_frames)))
                    if depth_state.get("depth_status") != "ready":
                        heuristic.confirmation.depth_unavailable()
                    if sampled and sampled.get("sequence") != last_depth_pair_sequence:
                        last_depth_pair_sequence = sampled["sequence"]
                        depth_state.update(self.depth_sample_state(sampled, pose_frames))
                        if depth_state.get("depth_status") == "ready":
                            heuristic.confirmation.observe_depth(sampled["source_time"],
                                depth_state["fight_depth_pairs"], timestamp)
                else:
                    # Synthetic depth supports only the explicitly labelled demo.
                    heuristic.confirmation.observe_depth(timestamp, fight_depth_evidence(depth, people), timestamp)
                # Keep sampled depth in the pair's evidence history. Never attach
                # old geometry to the current pose or treat relative Z as metres.
                for person in people:
                    person.depth = None
                result = heuristic.update([p for p in people if p.track_id >= 0], timestamp, flow, camera)
                checking = result.signals.get("pending_pairs", [])
                if checking and eco_enabled:
                    eco_gate.keep_active(timestamp)
                named_active = bool(result.events or result.state in {
                    "possible_fight", "person_down", "possible_fall", "person_down_after_fight", "hands_up"}
                    or (threat_alert and timestamp <= threat_until))
                unusual = motion_outlier.update(timestamp, flow, camera,
                    sum(p.track_id >= 0 and p.pose_reliable for p in people), named_active) if settings.mode == "live" else None
                if settings.mode == "live":
                    # These workers receive the same raw frame after pose/motion has
                    # decided whether it needs priority. No result blocks this loop.
                    priority = bool(result.events or unusual)
                    if self.depth_worker:
                        if priority:
                            self.depth_worker.submit(seq, timestamp, frame, priority=True)
                        elif not eco_enabled or eco_gate.should_run("depth", timestamp):
                            self.depth_worker.submit(seq, timestamp, frame)
                    if self.threat_worker:
                        if priority:
                            self.threat_worker.submit(seq, timestamp, frame, priority=True)
                        elif not getattr(self.capture, "on_frame", None):
                            self.threat_worker.submit(seq, timestamp, frame)
                if result.state in {"possible_fight", "person_down", "possible_fall", "person_down_after_fight", "hands_up"} or result.events:
                    alert = {"event_type": result.event_type, "label": EVENT_LABELS[result.event_type], "reasons": result.reasons}
                    alert_until = timestamp + 5
                if timestamp > alert_until or camera > heuristic.fight.rules.camera_speed:
                    alert = None
                if threat_alert and timestamp <= threat_until:
                    alert = threat_alert
                if unusual:
                    alert = {"event_type": "other_anomaly", "label": "UNUSUAL MOTION / REVIEW",
                             "reasons": unusual["reasons"], "priority": "low", "detector": "motion_outlier"}
                    alert_until = timestamp + 5
                rendered = annotate(frame, people)
                if checking and not alert:
                    pending = max(checking, key=lambda item: item["seconds"])
                    label = f'CHECKING INTERACTION: {pending["seconds"]:.1f} / {pending["required_seconds"]:g}s'
                    cv2.putText(rendered, label, (12, 28), cv2.FONT_HERSHEY_SIMPLEX, .55, (230, 210, 120), 2)
                evidence_ok, evidence_jpeg = cv2.imencode(".jpg", evidence_frame_720p(rendered), [cv2.IMWRITE_JPEG_QUALITY, 78])
                if not evidence_ok:
                    raise RuntimeError("Could not encode the processed video frame")
                evidence_bytes = evidence_jpeg.tobytes()
                if alert:
                    color = (75, 65, 235) if alert["event_type"] in {"fight", "person_down_after_fight"} else (30, 175, 245)
                    cv2.rectangle(rendered, (0, 0), (rendered.shape[1], 66), (18, 18, 24), -1)
                    font_scale = min(.75, (rendered.shape[1]-20) / max(1, len(alert["label"])*20))
                    cv2.putText(rendered, alert["label"], (12, 28), cv2.FONT_HERSHEY_SIMPLEX, font_scale, color, 2)
                    cv2.putText(rendered, "DETECTION ALERT / REVIEW REQUIRED", (12, 53), cv2.FONT_HERSHEY_SIMPLEX, .42, (225, 225, 225), 1)
                    ok, encoded = cv2.imencode(".jpg", rendered, [cv2.IMWRITE_JPEG_QUALITY, 78])
                    if not ok:
                        raise RuntimeError("Could not encode the alert video frame")
                    jpeg = encoded.tobytes()
                else:
                    jpeg = evidence_bytes
                buffer.append(timestamp, evidence_bytes)
                ratio = min(1, 640 / frame.shape[1])
                raw_size = (max(2, int(frame.shape[1] * ratio) // 2 * 2),
                            max(2, int(frame.shape[0] * ratio) // 2 * 2))
                raw = frame if raw_size == (frame.shape[1], frame.shape[0]) else cv2.resize(frame, raw_size)
                raw_ok, raw_jpeg = cv2.imencode(".jpg", raw, [cv2.IMWRITE_JPEG_QUALITY, 72])
                raw_bytes = raw_jpeg.tobytes() if raw_ok else None
                if raw_ok:
                    raw_buffer.append(timestamp, raw_bytes)
                evidence_recorder.append(timestamp, evidence_bytes, raw_bytes)
                finish_evidence(timestamp)
                now = time.time()
                if result.events or result.state in {"possible_fight", "person_down", "possible_fall", "person_down_after_fight", "hands_up"}:
                    last_event_at = timestamp
                for event in result.events:
                    incident_id, event_count, is_update = episodes.group(
                        event.event_type, uuid.uuid4().hex, timestamp, event.pair, people, frame.shape)
                    if is_update and incident_id not in evidence_recorder.pending:
                        episodes.release(incident_id)
                        incident_id, event_count, is_update = episodes.group(
                            event.event_type, uuid.uuid4().hex, timestamp, event.pair, people, frame.shape)
                    record = {"id": incident_id, "created": now, "mode": settings.mode,
                              "camera_id": self.camera_id, "camera_name": self.name,
                              "event_type": event.event_type, "score": event.score,
                              "reasons": event.reasons,
                              "signals": {**event.signals, "pair": event.pair,
                                          "source_seconds": timestamp,
                                          "buffer_seconds": round(timestamp-buffer.frames[0][0], 2)},
                              "event_count": event_count}
                    if is_update:
                        evidence_recorder.revise(record)
                    elif self.store.enqueue("incident", (record, buffer.snapshot(), raw_buffer.snapshot())):
                        evidence_recorder.begin(record, buffer.snapshot(), raw_buffer.snapshot())
                    else:
                        episodes.release(incident_id)
                if unusual:
                    last_event_at = timestamp
                    record = {"id": uuid.uuid4().hex, "created": now, "mode": settings.mode,
                              "camera_id": self.camera_id, "camera_name": self.name,
                              "event_type": "other_anomaly", "score": unusual["score"],
                              "reasons": unusual["reasons"], "event_count": 1,
                              "signals": {**unusual["signals"], "source_seconds": timestamp}}
                    if self.store.enqueue("incident", (record, buffer.snapshot(), raw_buffer.snapshot())):
                        evidence_recorder.begin(record, buffer.snapshot(), raw_buffer.snapshot())
                if (settings.mode == "live" and not checking and timestamp >= 15 and timestamp-last_normal_at >= NORMAL_SAMPLE_INTERVAL_SECONDS
                        and timestamp-last_event_at >= 10 and len(raw_buffer.frames) >= 2):
                    if self.store.enqueue("normal_sample", (self.camera_id, now, raw_buffer.snapshot())):
                        last_normal_at = timestamp
                if settings.mode == "live" and timestamp-last_model_check >= 1 and raw_buffer.frames and timestamp-raw_buffer.frames[0][0] >= 2:
                    last_model_check = timestamp
                    prediction = self.store.learner.predict(raw_buffer.snapshot(), self.camera_id)
                    # A clip classifier cannot bypass pair/depth confirmation for
                    # fight-related alerts; it remains available for other events.
                    if (prediction and prediction["label"] not in {"fight", "person_down_after_fight"}
                            and not alert and timestamp-last_event_at >= 8
                            and timestamp-last_learned_alert.get(prediction["label"], float("-inf")) >= 10):
                        last_learned_alert[prediction["label"]] = timestamp
                        last_event_at = timestamp
                        label = prediction["label"]
                        alert = {"event_type": label, "label": f"MODEL: POSSIBLE {label.replace('_', ' ').upper()}",
                                 "reasons": ["ResNet18 + GRU clip prediction; model score is uncalibrated; review required"]}
                        alert_until = timestamp + 5
                        record = {"id": uuid.uuid4().hex, "created": now, "mode": settings.mode,
                                  "camera_id": self.camera_id, "camera_name": self.name,
                                  "event_type": label, "score": min(1, prediction["margin"]),
                                  "reasons": alert["reasons"], "signals": {"model_generated": True,
                                  "model_margin": prediction["margin"], "model_confidence": prediction.get("confidence"),
                                  "model_architecture": "resnet18_gru", "source_seconds": prediction.get("source_seconds", timestamp)}}
                        self.store.enqueue("incident", (record, buffer.snapshot(), raw_buffer.snapshot()))
                if now-last_telemetry >= 1:
                    self.store.enqueue("telemetry", (now, len(people), result.score, settings.mode, self.camera_id))
                    last_telemetry = now
                elapsed = max(.001, time.monotonic()-cycle)
                source_sample_fps = 1/(timestamp-previous_source) if previous_source is not None and timestamp>previous_source else 0
                if settings.mode == "live":
                    skipped_frames += max(0, seq - (previous_sequence or 0) - 1)
                previous_sequence = seq
                processed_frames += 1
                if eco_enabled:
                    eco_processed_frames += 1
                    if eco_snapshot["state"] == "quiet":
                        eco_quiet_pose_frames += 1
                    else:
                        eco_active_pose_frames += 1
                processed_times.append(time.monotonic())
                while processed_times and processed_times[-1] - processed_times[0] > 5:
                    processed_times.popleft()
                processed_fps = ((len(processed_times)-1) / (processed_times[-1]-processed_times[0])
                                 if len(processed_times) > 1 and processed_times[-1] > processed_times[0] else 0)
                previous_source = timestamp
                with self.lock:
                    self.state["frame_identity"] = self.capture.identity(seq) if self.capture and hasattr(self.capture, "identity") else {}
                    self.frames = {"pose": jpeg, "depth": depth_jpeg or self.frames.get("depth"), "threat": self.frames.get("threat")}
                    eco = eco_gate.snapshot() if getattr(settings, "eco_mode", False) else None
                    self.state.update(status="running", message=("Synthetic inputs" if settings.mode == "demo" else "Eco mode · full analysis" if eco and eco["state"] == "active" else "Processing locally"), people=len(people), score=result.score, assessment=result.state, alert=alert, reasons=result.reasons, signals=result.signals, fps=round(processed_fps, 1), processed_fps=round(processed_fps, 1), last_processed_fps=round(processed_fps, 1) if processed_frames >= 30 else None, source_sample_fps=round(source_sample_fps, 1), processed_frames=processed_frames, skipped_frames=skipped_frames, capture_to_processed_ms=round((time.monotonic()-timestamp)*1000) if settings.mode == "live" and self.capture and not getattr(self.capture, "file", False) else None, latency_ms=round(elapsed*1000), sequence=seq, source_time=timestamp, last_frame_at=now, buffer_seconds=round(timestamp-buffer.frames[0][0], 1), depth_available=self.frames.get("depth") is not None, scenario=self.scenario, sampling_limited=settings.mode == "live" and source_sample_fps > 0 and source_sample_fps < 1/heuristic.fight.rules.max_gap_seconds, eco_mode=bool(eco), eco_state=eco["state"] if eco else "off", eco_motion_score=eco["motion_score"] if eco else 0, eco_skipped_frames=eco_skipped_frames, eco_processed_frames=eco_processed_frames, eco_active_pose_frames=eco_active_pose_frames, eco_quiet_pose_frames=eco_quiet_pose_frames, eco_active_seconds=round(eco_active_seconds, 1), eco_quiet_seconds=round(eco_quiet_seconds, 1), **depth_state)
                self.stop_event.wait(max(0, 1/settings.target_fps - elapsed))
        except Exception as exc:
            message = str(exc) if isinstance(exc, RuntimeError) else f"Pipeline failed ({type(exc).__name__}). Check installed vision dependencies and model files."
            with self.lock:
                self.state.update(status="error", message=message, fps=0, processed_fps=0)
        finally:
            if self.capture and hasattr(self.capture, "on_frame"):
                self.capture.on_frame = None
            finish_evidence(previous_source or 0, final=True)
            if self.threat_worker:
                self.threat_worker.stop()
            if self.depth_worker:
                self.depth_worker.stop()
            if self.capture:
                self.capture.stop()
            if self.stop_event.is_set():
                with self.lock:
                    self.state.update(status="idle", message="Session stopped", fps=0, processed_fps=0)
