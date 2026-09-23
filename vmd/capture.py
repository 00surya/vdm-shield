"""Latest-frame capture avoids building up latency behind neural inference."""
from collections import deque
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit
import cv2


def validate_source(source):
    if source.isdecimal():
        index = int(source)
        if index > 9:
            raise ValueError("Camera index must be between 0 and 9")
        return index, False
    parsed = urlsplit(source)
    if parsed.scheme in {"http", "https", "rtsp", "rtsps"}:
        if not parsed.hostname:
            raise ValueError("Stream URL needs a hostname")
        return source, False
    path = Path(source).expanduser()
    if path.is_file() and path.suffix.lower() in {".mp4", ".avi", ".mov", ".mkv", ".webm", ".m4v"}:
        return str(path.resolve()), True
    raise ValueError("Use a camera index, HTTP/RTSP stream URL, or an existing local video path")


class Capture:
    def __init__(self, source):
        self.source, self.file = validate_source(source)
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.packet = None
        self.on_frame = None
        self.session_id = uuid.uuid4().hex
        self.identities = {}
        self.evidence = deque()
        self.evidence_bytes = 0
        self.error = None
        self.finished = False
        self.duration_seconds = None
        self.source_fps = None
        self.captured_frames = 0
        self.capture_times = deque()
        self.thread = threading.Thread(target=self.run, daemon=True, name="camera-capture")

    def start(self):
        self.thread.start()

    def latest(self, after):
        with self.lock:
            return self.packet if self.packet is not None and self.packet[0] > after else None

    def identity(self, sequence):
        with self.lock:
            return dict(self.identities.get(sequence, {}))

    def evidence_between(self, source_time, before=10, after=4):
        with self.lock:
            return [(stamp, jpeg) for stamp, jpeg in self.evidence
                    if source_time - before <= stamp <= source_time + after]

    def stats(self):
        with self.lock:
            now = time.monotonic()
            while self.capture_times and now - self.capture_times[0] > 5:
                self.capture_times.popleft()
            times = self.capture_times
            rate = (len(times) - 1) / (times[-1] - times[0]) if len(times) > 1 and times[-1] > times[0] else 0
            return {"source_fps": self.source_fps, "captured_frames": self.captured_frames,
                    "capture_fps": round(rate, 1)}

    def run(self):
        cap = None
        try:
            if isinstance(self.source, str) and not self.file:
                cap = cv2.VideoCapture(self.source, cv2.CAP_FFMPEG, [cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 5000, cv2.CAP_PROP_READ_TIMEOUT_MSEC, 5000])
            else:
                cap = cv2.VideoCapture(self.source)
            if not cap.isOpened():
                raise RuntimeError("Cannot open input. Check the camera URL, network, permissions, or file.")
            fps = cap.get(cv2.CAP_PROP_FPS)
            fps = fps if 1 <= fps <= 240 else 25
            self.source_fps = round(fps, 2)
            if self.file:
                total_frames = cap.get(cv2.CAP_PROP_FRAME_COUNT)
                if total_frames > 0:
                    self.duration_seconds = total_frames / fps
            start = time.monotonic()
            sequence = 0
            previous_pts = -1
            while not self.stop_event.is_set():
                ok, frame = cap.read()
                if not ok:
                    if self.file:
                        break
                    raise RuntimeError("Camera disconnected or no frame arrived within the read timeout. Reconnect the source.")
                sequence += 1
                captured_at = time.monotonic()
                pts = cap.get(cv2.CAP_PROP_POS_MSEC) / 1000 if self.file else None
                timestamp = (pts if pts is not None and pts >= 0 and pts > previous_pts else (sequence-1)/fps) if self.file else captured_at
                if self.file:
                    timestamp = max(timestamp, previous_pts)
                    previous_pts = timestamp
                if self.file and self.stop_event.wait(max(0, start+timestamp-time.monotonic())):
                    break
                # Keep aspect ratio and even dimensions for evidence encoding.
                scale = min(1, 960/frame.shape[1])
                w,h = max(2, int(frame.shape[1]*scale)//2*2), max(2, int(frame.shape[0]*scale)//2*2)
                frame = cv2.resize(frame, (w,h))
                ratio = min(1, 640/frame.shape[1])
                raw = cv2.resize(frame, (max(2, int(frame.shape[1]*ratio)//2*2), max(2, int(frame.shape[0]*ratio)//2*2)))
                encoded_ok, encoded = cv2.imencode(".jpg", raw, [cv2.IMWRITE_JPEG_QUALITY, 75])
                with self.lock:
                    self.identities[sequence] = {"session_id": self.session_id, "frame_id": sequence,
                        "source_seconds": timestamp, "stream_seconds": timestamp if self.file else captured_at-start,
                        "time_basis": "media" if self.file else "local_capture", "captured_monotonic": captured_at,
                        "captured_at": time.time()}
                    while len(self.identities) > 1024:
                        self.identities.pop(next(iter(self.identities)))
                    if encoded_ok:
                        blob = encoded.tobytes()
                        self.evidence.append((timestamp, blob))
                        self.evidence_bytes += len(blob)
                        while self.evidence and (timestamp-self.evidence[0][0] > 20 or self.evidence_bytes > 64*1024*1024):
                            self.evidence_bytes -= len(self.evidence.popleft()[1])
                    self.packet = (sequence, timestamp, frame)
                    self.captured_frames = sequence
                    self.capture_times.append(time.monotonic())
                    while self.capture_times and self.capture_times[-1] - self.capture_times[0] > 5:
                        self.capture_times.popleft()
                if self.on_frame:
                    self.on_frame(sequence, timestamp, frame)
        except Exception as exc:
            # Do not echo URLs/credentials from lower-level exception strings.
            self.error = str(exc) if isinstance(exc, RuntimeError) else f"Capture failed ({type(exc).__name__})"
        finally:
            if cap is not None:
                cap.release()
            self.finished = True

    def stop(self):
        self.stop_event.set()
        self.thread.join(timeout=6)
