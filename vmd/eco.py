"""Conservative motion-aware scheduling for live cameras.

Motion controls the expensive pose/depth rate, but never becomes the only path
to a weapon alert. Quiet scenes keep periodic pose and threat safety scans.
"""
import threading

import cv2
import numpy as np


class EcoGate:
    ACTIVE_HOLD_SECONDS = 10.0
    QUIET_POSE_INTERVAL = 1.0
    QUIET_THREAT_INTERVAL = 1.0
    QUIET_DEPTH_INTERVAL = 10.0

    def __init__(self, motion_ratio=.008, pixel_delta=12):
        self.motion_ratio = motion_ratio
        self.pixel_delta = pixel_delta
        self.lock = threading.Lock()
        self.previous = None
        self.background = None
        self.active_until = float("-inf")
        self.last = {"pose": float("-inf"), "threat": float("-inf"), "depth": float("-inf")}
        self.motion_score = 0.0
        self.state = "starting"

    @staticmethod
    def _small_gray(frame):
        height, width = frame.shape[:2]
        scale = min(1.0, 160 / max(width, 1), 90 / max(height, 1))
        size = (max(16, round(width * scale)), max(16, round(height * scale)))
        gray = cv2.cvtColor(cv2.resize(frame, size, interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY)
        return cv2.GaussianBlur(gray, (5, 5), 0)

    def observe(self, frame, timestamp):
        gray = self._small_gray(frame)
        with self.lock:
            if self.previous is None:
                self.previous = gray
                self.background = gray.astype(np.float32)
                self.active_until = timestamp + self.ACTIVE_HOLD_SECONDS
                self.motion_score = 1.0
                self.state = "active"
                return True
            immediate = cv2.absdiff(gray, self.previous)
            background = cv2.absdiff(gray, cv2.convertScaleAbs(self.background))
            changed = np.maximum(immediate, background)
            ratio = float(np.count_nonzero(changed >= self.pixel_delta) / changed.size)
            mean_delta = float(changed.mean() / 255)
            score = min(1.0, max(ratio / max(self.motion_ratio, 1e-6), mean_delta / .025))
            if ratio >= self.motion_ratio or mean_delta >= .025:
                self.active_until = max(self.active_until, timestamp + self.ACTIVE_HOLD_SECONDS)
            cv2.accumulateWeighted(gray, self.background, .025)
            self.previous = gray
            self.motion_score = score
            active = timestamp <= self.active_until
            self.state = "active" if active else "quiet"
            return active

    def should_run(self, worker, timestamp):
        intervals = {"pose": self.QUIET_POSE_INTERVAL, "threat": self.QUIET_THREAT_INTERVAL,
                     "depth": self.QUIET_DEPTH_INTERVAL}
        if worker not in intervals:
            raise ValueError("Unknown Eco worker")
        with self.lock:
            if self.state != "quiet" or timestamp - self.last[worker] >= intervals[worker]:
                self.last[worker] = timestamp
                return True
            return False

    def keep_active(self, timestamp):
        """Do not reduce sampling while an interaction is being checked."""
        with self.lock:
            self.active_until = max(self.active_until, timestamp + self.ACTIVE_HOLD_SECONDS)
            self.state = "active"

    def snapshot(self):
        with self.lock:
            return {"state": self.state, "motion_score": round(self.motion_score, 3),
                    "quiet_pose_fps": 1 / self.QUIET_POSE_INTERVAL,
                    "quiet_threat_fps": 1 / self.QUIET_THREAT_INTERVAL,
                    "quiet_depth_fps": 1 / self.QUIET_DEPTH_INTERVAL,
                    "active_hold_seconds": self.ACTIVE_HOLD_SECONDS}
