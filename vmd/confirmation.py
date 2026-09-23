"""Confirm a tracked interaction using five seconds of motion and sampled depth.

Depth observations belong to their original frame. They support a pair's history,
never become geometry attached to a newer pose, and never establish intent.
"""
from collections import Counter
from dataclasses import dataclass, replace
from math import isfinite

from .spatial import box_overlap


@dataclass
class PendingInteraction:
    since: float
    last: float
    last_positive: float
    positive: bool = True
    supported_seconds: float = 0.0
    depth_since: float | None = None
    depth_last: float | None = None
    depth_samples: int = 0
    depth_status: str = "waiting"
    reported: bool = False


class FightConfirmation:
    minimum_seconds = 5.0
    minimum_overlap = .05  # Intersection / smaller person's box area.
    depth_max_gap = 2.5

    def __init__(self, rules):
        self.rules = rules
        self.required_seconds = max(self.minimum_seconds, rules.hold_seconds)
        self.pending = {}
        self.cooldowns = {}
        self.last_time = None
        self.last_depth_time = float("-inf")

    @staticmethod
    def _clear_depth(state, status):
        state.depth_since = state.depth_last = None
        state.depth_samples = 0
        state.depth_status = status
        state.supported_seconds = 0.0
        state.positive = False

    def depth_unavailable(self):
        for state in self.pending.values():
            self._clear_depth(state, "unavailable")

    def observe_depth(self, source_time, observations, current_time):
        """Accept a new, matched sample; repeated polling cannot advance the clock."""
        if (not isfinite(source_time) or source_time <= self.last_depth_time
                or not 0 <= current_time - source_time <= self.depth_max_gap):
            return
        self.last_depth_time = source_time
        pairs = {tuple(sorted(item["tracks"])): item for item in observations}
        for pair, state in self.pending.items():
            # A delayed sample from before a lost/reacquired pair cannot support it.
            if source_time < state.since:
                continue
            reading = pairs.get(pair)
            status = reading["status"] if reading else "uncertain"
            if status != "compatible":
                self._clear_depth(state, status)
                continue
            if state.depth_last is None or source_time - state.depth_last > self.depth_max_gap:
                state.depth_since = source_time
                state.depth_samples = 0
                state.supported_seconds = 0.0
                state.positive = False
            state.depth_last = source_time
            state.depth_samples += 1
            state.depth_status = "compatible"

    def update(self, assessment, people, timestamp, camera_motion):
        rules = self.rules
        dt = timestamp - self.last_time if self.last_time is not None else 0
        valid_time = 0 < dt <= rules.max_gap_seconds
        if not valid_time or camera_motion > rules.camera_speed:
            self.pending.clear()
        self.last_time = timestamp
        self.cooldowns = {pair: time for pair, time in self.cooldowns.items()
                          if timestamp - time < rules.cooldown_seconds}
        # Never let provisional rule events bypass confirmation.
        result = replace(assessment, state="observing", trigger=False, events=[], candidates=[],
                         signals={**assessment.signals, "active_fight_pairs": [],
                                  "confirmation_seconds": 0.0, "required_seconds": self.required_seconds,
                                  "pending_pairs": []})
        if camera_motion > rules.camera_speed:
            result.state = "camera_moving"
            return result
        counts = Counter(person.track_id for person in people)
        tracks = {person.track_id: person for person in people if person.track_id >= 0
                  and counts[person.track_id] == 1}
        visible, choices, progress, events = set(), [], [], []
        for candidate in assessment.candidates:
            pair = candidate.pair
            if not pair or pair[0] == pair[1] or any(track not in tracks for track in pair):
                continue
            a, b = (tracks[track] for track in pair)
            overlap = box_overlap(a.box, b.box)
            poses_ok = a.pose_reliable and b.pose_reliable
            flow = min(a.local_flow or 0, b.local_flow or 0)
            positive = (valid_time and poses_ok and overlap >= self.minimum_overlap
                        and flow >= rules.flow_speed and candidate.signals.get("candidate", False))
            state = self.pending.get(pair)
            # Geometric/identity breaks are hard resets; a short motion reversal is
            # tolerated, but contributes no time to the five seconds of evidence.
            if not valid_time or not poses_ok or overlap < self.minimum_overlap:
                self.pending.pop(pair, None)
                state = None
            elif not positive and state and timestamp - state.last_positive > rules.signal_grace_seconds:
                self.pending.pop(pair, None)
                state = None
            elif positive and state is None:
                state = PendingInteraction(timestamp, timestamp, timestamp)
                self.pending[pair] = state
            if state:
                visible.add(pair)
                if positive and state.positive:
                    state.supported_seconds += max(0, timestamp - state.last)
                if positive:
                    state.last_positive = timestamp
                state.last, state.positive = timestamp, positive
                if state.depth_last is not None and timestamp - state.depth_last > self.depth_max_gap:
                    self._clear_depth(state, "stale")
                # Depth must cover the confirmation window with several separate
                # samples. One matching sample is not five seconds of evidence.
                depth_seconds = (state.depth_last - state.depth_since
                                 if state.depth_last is not None else 0.0)
                supported = min(state.supported_seconds, depth_seconds)
                confirmed = (positive and supported + 1e-9 >= self.required_seconds
                             and state.depth_samples >= 3 and state.depth_status == "compatible")
            else:
                supported, confirmed = 0.0, False
            blockers = list(candidate.signals.get("blockers", []))
            if overlap < self.minimum_overlap:
                blockers.append("The two person boxes must overlap")
            if flow < rules.flow_speed:
                blockers.append("Fast image motion is required inside both person boxes")
            if state and state.depth_status != "compatible":
                blockers.append(f"Waiting for reliable matching depth: {state.depth_status}")
            if state and not confirmed:
                blockers.append(f"Checking the same pair: {supported:.1f} / {self.required_seconds:g} seconds")
            signals = {**candidate.signals, "box_overlap": round(overlap, 3),
                       "both_people_flow": round(flow, 3), "required_seconds": self.required_seconds,
                       "confirmation_seconds": round(supported, 2),
                       "motion_supported_seconds": round(state.supported_seconds, 2) if state else 0.0,
                       "depth_status": state.depth_status if state else "waiting",
                       "depth_samples": state.depth_samples if state else 0,
                       "depth_source_time": state.depth_last if state else None,
                       "blockers": [] if confirmed else blockers}
            reasons = list(candidate.reasons)
            if confirmed:
                reasons += ["Overlapping tracked people with motion inside both boxes",
                            "Matching relative-depth samples support the same pair",
                            f"Interaction evidence accumulated for at least {self.required_seconds:g} seconds; review required"]
            choice = replace(candidate, state="possible_fight" if confirmed else "checking_interaction" if state else "observing",
                             reasons=reasons, signals=signals, trigger=False, events=[], candidates=[])
            if confirmed and not state.reported and pair not in self.cooldowns:
                choice.trigger = True
                events.append(replace(choice))
                state.reported = True
                self.cooldowns[pair] = timestamp
            choices.append(choice)
            if state:
                progress.append({"tracks": list(pair), "seconds": round(supported, 2),
                                 "required_seconds": self.required_seconds, "depth_status": state.depth_status,
                                 "state": choice.state})
        self.pending = {pair: state for pair, state in self.pending.items() if pair in visible}
        if choices:
            result = max(choices, key=lambda item: (item.state == "possible_fight",
                         item.state == "checking_interaction", item.signals["confirmation_seconds"], item.score))
        result.events = events
        result.signals = {**result.signals, "pending_pairs": progress,
                          "required_seconds": self.required_seconds,
                          "active_fight_pairs": [item.pair for item in choices if item.state == "possible_fight"]}
        return result
