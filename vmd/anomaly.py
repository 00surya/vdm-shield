"""Conservative, camera-local motion outlier signal for operator review."""
from collections import deque
from statistics import median


class MotionOutlier:
    def __init__(self, warmup_seconds=5, hold_seconds=1.5, cooldown_seconds=30):
        self.warmup_seconds = warmup_seconds
        self.hold_seconds = hold_seconds
        self.cooldown_seconds = cooldown_seconds
        self.history = deque()
        self.started = None
        self.candidate_since = None
        self.last_alert = float("-inf")
        self.last_time = None

    def update(self, timestamp, person_flow, camera_motion, visible_people, named_active=False):
        if self.last_time is not None and not 0 < timestamp - self.last_time <= .65:
            self.candidate_since = None
        self.last_time = timestamp
        if self.started is None:
            self.started = timestamp
        while self.history and timestamp - self.history[0][0] > 30:
            self.history.popleft()
        if camera_motion > .09 or not visible_people or named_active:
            self.candidate_since = None
            return None
        typical = median(value for _, value in self.history) if self.history else 0
        threshold = max(.06, typical * 2.5 + .025)
        calibrated = timestamp - self.started >= self.warmup_seconds and len(self.history) >= 15
        if calibrated and person_flow >= threshold:
            if self.candidate_since is None:
                self.candidate_since = timestamp
            held = timestamp - self.candidate_since
            if held >= self.hold_seconds and timestamp - self.last_alert >= self.cooldown_seconds:
                self.last_alert = timestamp
                self.candidate_since = None
                return {"score": round(min(1, person_flow / threshold * .35), 3),
                        "reasons": [f"Person-region motion stayed above this camera's recent baseline for {held:.1f} seconds",
                                    "Unclassified motion pattern; review the evidence to identify the activity"],
                        "signals": {"local_flow": round(person_flow, 3), "baseline_flow": round(typical, 3),
                                    "motion_threshold": round(threshold, 3), "held_seconds": round(held, 2),
                                    "priority": "low", "detector": "motion_outlier"}}
        else:
            self.candidate_since = None
        if person_flow < threshold:
            self.history.append((timestamp, person_flow))
        return None
