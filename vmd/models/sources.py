from dataclasses import asdict, dataclass, field
from pathlib import Path
import os
import json
import math
import sqlite3
import time
import threading
import uuid

from ..capture import validate_source
from ..engine import Engine
from ..storage import Store
from ..health_log import HealthLog


def default_target_fps():
    try:
        return min(60, max(1, int(os.environ.get("VMD_TARGET_FPS", "20"))))
    except ValueError:
        return 20


@dataclass
class SourceSettings:
    source: str
    name: str = ""
    kind: str = "camera"
    mode: str = "live"
    threshold: float = .60
    hold_seconds: float = 5.0
    target_fps: int = field(default_factory=default_target_fps)
    eco_mode: bool = False


class SourceManager:
    limit = 4

    def __init__(self, data_dir, model_dir, licence=None):
        self.licence = licence
        self.data_dir = Path(data_dir)
        self.uploads = self.data_dir / "uploads"
        self.uploads.mkdir(parents=True, exist_ok=True)
        self.store = Store(self.data_dir)
        self.store.learner.model_dir = Path(model_dir)
        self.model_dir = model_dir
        self.cameras = {}
        self.lock = threading.RLock()
        self.closed = False
        self.saved_path = self.data_dir / "cameras.json"
        self.desired = {}
        self.retry_at = {}
        self.retry_count = {}
        self.configuration_error = None
        self.shutdown = threading.Event()
        self.health_log = HealthLog(self.data_dir)
        self._restore()
        self.supervisor = threading.Thread(target=self._supervise, daemon=True, name="camera-recovery")
        self.supervisor.start()

    def permitted(self, camera_id):
        if self.licence is None:
            return True
        allowed = self.licence.allowance()
        ids = list(self.cameras)
        return allowed > 0 and (camera_id not in ids or ids.index(camera_id) < allowed)

    def _persist(self):
        temporary = self.saved_path.with_suffix(".tmp")
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as stream:
            os.fchmod(stream.fileno(), 0o600)
            json.dump(self.desired, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, self.saved_path)

    def _restore(self):
        if not self.saved_path.exists():
            return
        try:
            saved = json.loads(self.saved_path.read_text())
            if not isinstance(saved, dict) or len(saved) > self.limit:
                raise ValueError("Invalid camera configuration")
            for camera_id, entry in saved.items():
                settings = SourceSettings(**entry["settings"])
                if settings.kind != "camera":
                    continue
                validate_source(settings.source)
                engine = Engine(self.store, self.model_dir, camera_id, settings.name or "Camera")
                engine.licence_check = lambda identity=camera_id: self.permitted(identity)
                engine.settings = settings
                self.cameras[camera_id] = engine
                self.desired[camera_id] = entry
                if entry["enabled"] and self.permitted(camera_id):
                    engine.start(settings)
        except (OSError, ValueError, TypeError, KeyError):
            self.configuration_error = "Saved camera setup could not be fully restored. Check configuration."

    def recover_once(self):
        with self.lock:
            if self.closed:
                return
            for camera_id, entry in self.desired.items():
                if not entry["enabled"]:
                    continue
                engine = self.cameras[camera_id]
                if not self.permitted(camera_id):
                    engine.stop_event.set()
                    continue
                state = engine.snapshot()
                failed = state["status"] in {"error", "idle", "finished", "stopping"} or (
                    state.get("stale") and state.get("frame_age_seconds", 0) > 15)
                if not failed:
                    if state["status"] == "running" and not state.get("stale"):
                        self.retry_count[camera_id] = 0
                    continue
                if time.monotonic() < self.retry_at.get(camera_id, 0):
                    continue
                attempts = self.retry_count.get(camera_id, 0) + 1
                self.retry_count[camera_id] = attempts
                self.retry_at[camera_id] = time.monotonic() + min(300, 5 * 2 ** min(attempts, 6))
                try:
                    engine.stop()
                    engine.start(engine.settings)
                except (RuntimeError, OSError, ValueError):
                    pass

    def _supervise(self):
        while not self.shutdown.wait(5):
            self.recover_once()
            try:
                self.health_log.observe(self.list(), self.store.evidence_health())
            except (OSError, sqlite3.Error):
                pass  # A full/unavailable log disk must not stop camera recovery.

    def set_enabled(self, camera_id, enabled):
        with self.lock:
            engine = self.cameras.get(camera_id)
            if engine is None:
                raise ValueError("Source not found")
            if enabled and not self.permitted(camera_id):
                raise RuntimeError("Licence unavailable or source allowance exceeded. Open Activation & licence.")
            if camera_id in self.desired:
                old = self.desired[camera_id]["enabled"]
                self.desired[camera_id]["enabled"] = enabled
                try:
                    self._persist()
                except OSError:
                    self.desired[camera_id]["enabled"] = old
                    raise
            if enabled:
                engine.start(engine.settings)
            else:
                engine.stop()

    def set_target_fps(self, camera_id, value):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or int(value) != value or not 1 <= value <= 60:
            raise ValueError("Choose a whole-number analysis rate from 1 to 60 FPS.")
        value = int(value)
        with self.lock:
            engine = self.cameras.get(camera_id)
            if engine is None:
                raise KeyError(camera_id)
            if engine.settings is None or engine.settings.kind != "camera":
                raise ValueError("FPS settings apply to cameras only.")
            if camera_id in self.desired:
                previous = self.desired[camera_id]["settings"]["target_fps"]
                self.desired[camera_id]["settings"]["target_fps"] = value
                try:
                    self._persist()
                except OSError:
                    self.desired[camera_id]["settings"]["target_fps"] = previous
                    raise
            with engine.lock:
                engine.settings.target_fps = value
                engine.state["target_fps"] = value
            return value

    def set_eco_mode(self, camera_id, enabled):
        if not isinstance(enabled, bool):
            raise ValueError("Eco mode must be on or off.")
        with self.lock:
            engine = self.cameras.get(camera_id)
            if engine is None:
                raise KeyError(camera_id)
            if engine.settings is None or engine.settings.kind != "camera":
                raise ValueError("Eco mode applies to live cameras only.")
            if camera_id in self.desired:
                previous = self.desired[camera_id]["settings"].get("eco_mode", False)
                self.desired[camera_id]["settings"]["eco_mode"] = enabled
                try:
                    self._persist()
                except OSError:
                    self.desired[camera_id]["settings"]["eco_mode"] = previous
                    raise
            with engine.lock:
                engine.settings.eco_mode = enabled
                engine.state.update(eco_mode=enabled,
                                    eco_state="starting" if enabled else "off",
                                    eco_motion_score=0)
            return enabled

    def add(self, settings):
        validate_source(settings.source)
        with self.lock:
            if self.licence is not None and len(self.cameras) >= self.licence.allowance():
                raise ValueError("Licence unavailable or source allowance exceeded. Open Activation & licence.")
            if len(self.cameras) >= self.limit:
                raise ValueError("Remove a source before adding another (maximum four).")
            camera_id = "cam-" + uuid.uuid4().hex[:10]
            engine = Engine(self.store, self.model_dir, camera_id, settings.name.strip() or f"Source {len(self.cameras)+1}")
            engine.licence_check = lambda identity=camera_id: self.permitted(identity)
            engine.start(settings)
            self.cameras[camera_id] = engine
            if settings.kind == "camera":
                self.desired[camera_id] = {"settings": asdict(settings), "enabled": True}
                try:
                    self._persist()
                except OSError:
                    self.desired.pop(camera_id)
                    self.cameras.pop(camera_id)
                    engine.stop()
                    raise
            return engine.snapshot()

    def get(self, camera_id):
        with self.lock:
            return self.cameras.get(camera_id)

    def list(self):
        with self.lock:
            engines = list(self.cameras.values())
        return [engine.snapshot() for engine in engines]

    def remove(self, camera_id):
        engine = self.get(camera_id)
        if engine is None:
            return False
        self.set_enabled(camera_id, False)
        self.store.learner.forget_source(camera_id)
        if engine.thread and engine.thread.is_alive():
            raise RuntimeError("Source is still stopping. Please retry.")
        with self.lock:
            if camera_id in self.desired:
                entry = self.desired.pop(camera_id)
                try:
                    self._persist()
                except OSError:
                    self.desired[camera_id] = entry
                    raise
            self.cameras.pop(camera_id, None)
        return True

    def close(self):
        with self.lock:
            if self.closed:
                return
            self.closed = True
            engines = list(self.cameras.values())
        self.shutdown.set()
        self.supervisor.join(timeout=10)
        for engine in engines:
            engine.stop()
        self.store.close()
