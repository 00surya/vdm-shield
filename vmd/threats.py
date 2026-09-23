"""Local object detection and bounded asynchronous sampling."""
import hashlib
import time
from pathlib import Path

from .objects import ObjectModel
from .depth_worker import DepthWorker, replace_latest

WEIGHTS = "threat-yolov8n.pt"
REVISION = "c6d6fa4e6c9bfd4c4fccb46478db23609e5468fb"
SHA256 = "86c43444ae8319d2276dd300edc3e7f7a1137fe7566737f994ca579ad770f6ce"
MODEL_URL = f"https://huggingface.co/Subh775/Threat-Detection-YOLOv8n/resolve/{REVISION}/weights/best.pt"
LABELS = {"gun": "gun_detected", "knife": "knife_detected",
          "grenade": "grenade_detected", "explosion": "possible_explosion"}
ALIASES = {"gun": "gun", "knife": "knife", "grenade": "grenade",
           "explosion": "explosion", "explosive": "explosion"}


class ThreatModel:
    def __init__(self, model_dir, device="cpu"):
        import os
        path = Path(model_dir).resolve() / WEIGHTS
        if not path.is_file():
            raise RuntimeError("Threat weights missing. Run scripts/download_threat_model.py.")
        if hashlib.sha256(path.read_bytes()).hexdigest() != SHA256:
            raise RuntimeError("Threat weights checksum mismatch. Re-download the pinned model.")
        runtime = path.parent / ".runtime"
        os.environ["YOLO_CONFIG_DIR"] = str(runtime / "ultralytics")
        os.environ["YOLO_OFFLINE"] = "true"
        os.environ["YOLO_AUTOINSTALL"] = "false"
        os.environ.setdefault("MPLCONFIGDIR", str(runtime / "matplotlib"))
        from ultralytics import YOLO
        self.model = YOLO(str(path), task="detect")
        self.device = device
        names = self.model.names.values() if isinstance(self.model.names, dict) else self.model.names
        if {ALIASES.get(str(name).lower()) for name in names} != set(LABELS):
            raise RuntimeError("Unexpected threat model classes; detector disabled.")

    def infer(self, frame):
        result = self.model.predict(frame, imgsz=640, conf=.4, iou=.45,
                                    device=self.device, max_det=30, verbose=False)[0]
        detections = []
        if result.boxes is not None:
            for box, score, cls in zip(result.boxes.xyxy.cpu().tolist(),
                                       result.boxes.conf.cpu().tolist(), result.boxes.cls.cpu().tolist()):
                label = ALIASES.get(str(result.names[int(cls)]).lower())
                if label:
                    detections.append({"label": label, "confidence": round(float(score), 3),
                                       "box": [round(float(x), 1) for x in box]})
        return detections


def annotate_threats(frame, detections):
    import cv2
    image = frame.copy()
    for item in detections:
        x1, y1, x2, y2 = map(int, item["box"])
        color = (220, 150, 30) if item.get("context_only") else (40, 80, 240)
        cv2.rectangle(image, (x1, y1), (x2, y2), color, 2)
        cv2.putText(image, f'{item["label"].upper()} {item["confidence"]:.0%}',
                    (max(0, x1), max(18, y1 - 6)), cv2.FONT_HERSHEY_SIMPLEX,
                    .5, color, 2)
    return image


def run_threats(requests, responses, stop, device, model_dir):
    import queue
    try:
        import cv2
        import torch
        cv2.setNumThreads(1)
        torch.set_num_threads(1)
        model = ThreatModel(model_dir, device)
        objects, object_error = None, None
        try:
            objects = ObjectModel(model_dir, device)
        except Exception as exc:
            object_error = str(exc) if isinstance(exc, RuntimeError) else f'General detector failed ({type(exc).__name__})'
        replace_latest(responses, {"status": "ready"})
        while not stop.is_set():
            try:
                sequence, source_time, submitted_at, frame = requests.get(timeout=.1)
            except queue.Empty:
                continue
            started = time.monotonic()
            detections = model.infer(frame)
            scene_objects = []
            if objects is not None:
                try:
                    scene_objects = objects.infer(frame)
                except Exception as exc:
                    object_error = f'General detector failed ({type(exc).__name__}); weapon detection continues'
                    objects = None
            ok, jpeg = cv2.imencode(".jpg", annotate_threats(frame, scene_objects + detections))
            ratio = min(1, 640 / frame.shape[1])
            raw = cv2.resize(frame, (max(2, int(frame.shape[1] * ratio) // 2 * 2),
                                     max(2, int(frame.shape[0] * ratio) // 2 * 2)))
            raw_ok, raw_jpeg = cv2.imencode(".jpg", raw)
            if not ok:
                raise RuntimeError("Could not encode threat frame")
            replace_latest(responses, {"status": "ready", "sequence": sequence,
                "source_time": source_time, "submitted_at": submitted_at, "completed_monotonic": time.monotonic(), "completed_at": time.time(),
                "detections": detections, "scene_objects": scene_objects,
                "object_status": "ready" if objects is not None else "error", "object_error": object_error, "jpeg": jpeg.tobytes(),
                "raw_jpeg": raw_jpeg.tobytes() if raw_ok else None,
                "latency_ms": round((time.monotonic() - started) * 1000)})
    except Exception as exc:
        replace_latest(responses, {"status": "error", "error":
            str(exc) if isinstance(exc, RuntimeError) else f"Threat detection failed ({type(exc).__name__})"})


class ThreatWorker(DepthWorker):
    def __init__(self, device, model_dir, fps=2):
        super().__init__(device, model_dir, fps=fps, runner=run_threats)
        self.process.name = "vmd-threats"

    def poll(self):
        status, result, error = super().poll()
        if error == "Depth worker exited; pose and motion remain active":
            self.error = error = "Threat worker exited; other analysis remains active"
        return status, result, error


class ThreatAlerts:
    """First qualifying observation alerts immediately, with a per-class cooldown."""
    def __init__(self, cooldown=10):
        self.cooldown = cooldown
        self.last = {}

    def update(self, detections, timestamp):
        events = []
        strongest = {}
        for item in detections:
            label = item.get("label")
            if item.get("context_only") or label not in LABELS:
                continue
            threshold = .7 if label == "explosion" else .55
            if item["confidence"] >= threshold and item["confidence"] > strongest.get(label, {}).get("confidence", 0):
                strongest[label] = item
        for label, item in strongest.items():
            if timestamp - self.last.get(label, float("-inf")) >= self.cooldown:
                self.last[label] = timestamp
                events.append({"event_type": LABELS[label], **item})
        return events
