"""Rate-limited, isolated depth inference; never on the pose/motion critical path."""
import multiprocessing as mp
import os
import queue
import threading
import time
from collections import deque


def replace_latest(channel, value):
    """Bound memory to one pending item. Dropping on a feeder race is acceptable."""
    try:
        channel.put_nowait(value)
        return True
    except queue.Full:
        try:
            channel.get_nowait()
        except queue.Empty:
            return False
        try:
            channel.put_nowait(value)
            return True
        except queue.Full:
            return False


def run_depth(requests, responses, stop, device, model_dir):
    try:
        # Separate CPU pools avoid changing the pose model's global torch settings.
        if hasattr(os, "nice"):
            try:
                os.nice(5)
            except OSError:
                pass
        import cv2
        from .vision import DepthModel, depth_view
        cv2.setNumThreads(1)
        model = DepthModel(device, model_dir)
        replace_latest(responses, {"status": "ready"})
        while not stop.is_set():
            try:
                sequence, source_time, submitted_at, frame = requests.get(timeout=.1)
            except queue.Empty:
                continue
            if stop.is_set():
                break
            started = time.monotonic()
            depth = model.infer(frame)
            ok, encoded = cv2.imencode(".jpg", depth_view(depth))
            if not ok:
                raise RuntimeError("Could not encode depth image")
            replace_latest(responses, {
                "status": "ready", "sequence": sequence, "source_time": source_time,
                "submitted_at": submitted_at, "completed_monotonic": time.monotonic(), "completed_at": time.time(), "jpeg": encoded.tobytes(),
                "depth": depth.astype("float16"),
                "model": model.name, "backend": model.backend,
                "latency_ms": round((time.monotonic()-started)*1000),
            })
    except Exception as exc:
        message = str(exc) if isinstance(exc, RuntimeError) else f"Depth failed ({type(exc).__name__}); check depth model dependencies"
        replace_latest(responses, {"status": "error", "error": message})


class DepthWorker:
    def __init__(self, device, model_dir, fps=1, runner=run_depth):
        context = mp.get_context("spawn")
        self.requests = context.Queue(maxsize=1)
        self.responses = context.Queue(maxsize=1)
        self.stop_event = context.Event()
        self.interval = 1/fps
        self.last_submit = float("-inf")
        self.priority_sequence = None
        self.priority_at = None
        self.status = "loading"
        self.error = None
        self.result = None
        self.closed = False
        self.identities = {}
        self.completed_times = deque(maxlen=120)
        self.completed_count = 0
        self.process = context.Process(target=runner, args=(self.requests, self.responses, self.stop_event, device, str(model_dir)), daemon=True, name="vmd-depth")

    def start(self):
        self.process.start()

    def submit(self, sequence, source_time, frame, priority=False, identity=None):
        if not hasattr(self, "submission_lock"):
            self.submission_lock = threading.RLock()
        with self.submission_lock:
            return self._submit(sequence, source_time, frame, priority, identity)

    def _submit(self, sequence, source_time, frame, priority=False, identity=None):
        now = time.monotonic()
        if self.closed or self.status == "error":
            return False
        if self.priority_sequence is not None and now - self.priority_at > 5:
            self.priority_sequence = self.priority_at = None
        if priority and self.priority_sequence == sequence:
            return True
        if self.priority_sequence is not None or (not priority and now-self.last_submit < self.interval):
            return False
        # Own the queued frame; upstream code may reuse its capture buffer.
        if not replace_latest(self.requests, (sequence, source_time, time.time(), frame.copy())):
            return False
        if not hasattr(self, "identities"):
            self.identities = {}
        provider = getattr(self, "identity_provider", None)
        self.identities[sequence] = dict(identity or (provider(sequence) if provider else {}))
        while len(self.identities) > 128:
            self.identities.pop(next(iter(self.identities)))
        self.last_submit = now
        if priority:
            self.priority_sequence, self.priority_at = sequence, now
        return True

    def poll(self):
        if self.closed:
            return self.status, self.result, self.error
        while True:
            try:
                update = self.responses.get_nowait()
            except queue.Empty:
                break
            self.status = update["status"]
            self.error = update.get("error")
            if "jpeg" in update:
                identity = self.identities.pop(update["sequence"], {})
                update["identity"] = identity
                update.setdefault("completed_at", time.time())
                update["capture_to_result_ms"] = round((update.get("completed_monotonic", time.monotonic())-identity["captured_monotonic"])*1000, 1) if identity.get("captured_monotonic") is not None else None
                if not hasattr(self, "completed_times"):
                    self.completed_times = deque(maxlen=120)
                    self.completed_count = 0
                self.completed_times.append(update.get("completed_monotonic", time.monotonic()))
                self.completed_count += 1
                self.result = update
                if update.get("sequence") == self.priority_sequence:
                    self.priority_sequence = self.priority_at = None
            if self.status == "error":
                self.priority_sequence = self.priority_at = None
        if self.process.exitcode is not None and self.status != "error":
            self.status, self.error = "error", "Depth worker exited; pose and motion remain active"
        return self.status, self.result, self.error

    def metrics(self):
        now = time.monotonic()
        times = [stamp for stamp in tuple(self.completed_times) if now-stamp <= 10]
        rate = (len(times)-1)/(times[-1]-times[0]) if len(times)>1 and times[-1]>times[0] else 0
        result = self.result or {}
        return {"status": self.status, "completed_fps": round(rate, 2),
                "target_fps": 1/self.interval, "completed_samples": self.completed_count,
                "inference_ms": result.get("latency_ms"),
                "capture_to_result_ms": result.get("capture_to_result_ms"),
                "result_age_seconds": round(now-self.completed_times[-1], 2) if self.completed_times else None,
                "identity": result.get("identity")}

    def stop(self):
        if self.closed:
            return
        self.stop_event.set()
        if self.process.pid is not None:
            self.process.join(timeout=.25)
            if self.process.is_alive():
                self.process.terminate()
                self.process.join(timeout=2)
        self.closed = True
        self.status = "stopped"
        for channel in (self.requests, self.responses):
            channel.cancel_join_thread()
            channel.close()
