"""Temporal posture events associated only with the same camera and track."""
from collections import deque
from dataclasses import replace
from math import hypot
from statistics import median

from .heuristics import Assessment, FightHeuristic
from .confirmation import FightConfirmation

EVENT_LABELS = {
    "fight": "POSSIBLE FIGHT / REVIEW",
    "person_down": "PERSON DOWN",
    "possible_fall": "POSSIBLE FALL",
    "person_down_after_fight": "PERSON DOWN AFTER FIGHT SIGNAL",
    "hands_up": "HANDS UP / REVIEW",
}


def centroid_speed(history):
    """Robust pixel/second displacement over a short window, not one noisy box step."""
    if len(history) < 3 or history[-1][0] - history[0][0] < .4:
        return None
    count = max(1, len(history) // 3)
    first, last = list(history)[:count], list(history)[-count:]
    dt = median(p[0] for p in last) - median(p[0] for p in first)
    if dt <= 0:
        return None
    return hypot(*(median(p[axis] for p in last) - median(p[axis] for p in first)
                   for axis in (1, 2))) / dt


class BehaviorHeuristic:
    def __init__(self, rules=None):
        self.fight = FightHeuristic(rules)
        self.confirmation = FightConfirmation(self.fight.rules)
        self.tracks = {}
        self.hands = {}
        self.recent_fights = {}
        self.last_time = None

    def update(self, people, timestamp, local_flow=0, camera_motion=0):
        result = self.fight.update(people, timestamp, local_flow, camera_motion)
        result = self.confirmation.update(result, people, timestamp, camera_motion)
        pending_pairs = result.signals.get("pending_pairs", [])
        events = list(result.events)
        if self.last_time is not None and not 0 < timestamp - self.last_time <= self.fight.rules.max_gap_seconds:
            self.tracks.clear()
            self.hands.clear()
            self.recent_fights.clear()
        self.last_time = timestamp
        if result.state == "camera_moving":
            self.tracks.clear()
            self.hands.clear()
            self.recent_fights.clear()
            return result
        for pair in result.signals.get("active_fight_pairs", []):
            for track in pair:
                self.recent_fights[track] = timestamp
        for signal in [result, *events]:
            if signal.state == "possible_fight" and signal.pair:
                for track in signal.pair:
                    self.recent_fights[track] = timestamp
        self.recent_fights = {key: time for key, time in self.recent_fights.items() if timestamp - time < 8}
        active = set()
        current_postures = []
        for person in people:
            active.add(person.track_id)
            points = person.keypoints
            # COCO uses wrists 9/10 and nose 0; 15/16 are ankles.
            raised = (person.pose_reliable and all(points[i][2] >= .55 for i in (0, 9, 10))
                      and all(points[i][1] < points[0][1] - person.scale * .03 for i in (9, 10)))
            if raised:
                hands = self.hands.setdefault(person.track_id, {"since": timestamp, "reported": False})
                held = timestamp - hands["since"]
                if held >= 2:
                    signal = Assessment(.75, "hands_up", ["Both wrists stayed above the nose for at least 2 seconds",
                        "Hands-up posture alone does not establish robbery or surrender"],
                        {"track_id": person.track_id, "raised_seconds": round(held, 2)},
                        (person.track_id, person.track_id), not hands["reported"], "hands_up")
                    if signal.trigger:
                        events.append(signal)
                        hands["reported"] = True
                    current_postures.append(replace(signal, trigger=False))
            else:
                self.hands.pop(person.track_id, None)
            previous = self.tracks.get(person.track_id, {})
            if not person.pose_reliable or any(points[i][2] < .55 for i in (5, 6, 11, 12)):
                self.tracks.pop(person.track_id, None)
                continue
            shoulder = tuple((points[5][axis] + points[6][axis]) / 2 for axis in (0, 1))
            hip = tuple((points[11][axis] + points[12][axis]) / 2 for axis in (0, 1))
            dx, dy = abs(hip[0] - shoulder[0]), abs(hip[1] - shoulder[1])
            x1, _, x2, _ = person.box
            upright = dy > dx * 1.5 and dy > person.scale * .12
            prone = dx > dy * 1.5 and dx > person.scale * .12 and (x2 - x1) > person.height * 1.2
            state = dict(previous)
            centers = state.setdefault("centers", deque())
            centers.append((timestamp, *person.center))
            while centers and timestamp - centers[0][0] > 1:
                centers.popleft()
            speed = centroid_speed(centers)
            if upright:
                reference = state.get("upright")
                if not reference or timestamp - reference[0] > 1:
                    state["upright"] = (timestamp, shoulder[1], person.scale)
                for key in ("prone_since", "stationary_since", "reported", "assessment", "fall"):
                    state.pop(key, None)
            elif prone:
                state.setdefault("prone_since", timestamp)
                reference = state.get("upright")
                if reference and 0 < timestamp - reference[0] <= 2 and (shoulder[1] - reference[1]) / reference[2] > .25:
                    state["fall"] = True
                if speed is not None and speed < 5:
                    state.setdefault("stationary_since", timestamp)
                else:
                    state.pop("stationary_since", None)
                still = timestamp - state.get("stationary_since", timestamp)
                if still >= 3 and not state.get("reported"):
                    after_fight = person.track_id in self.recent_fights and state.get("fall", False)
                    event_type = "person_down_after_fight" if after_fight else "possible_fall" if state.get("fall") else "person_down"
                    reasons = ["Horizontal torso and body aspect ratio above 1.2",
                               "Centroid movement below 5 pixels/second for at least 3 seconds"]
                    if state.get("fall"):
                        reasons.append("Rapid downward transition from a recently upright posture")
                    if after_fight:
                        reasons.append("The same track was involved in a recent fight signal; causation is unconfirmed")
                    signal = Assessment(.8, event_type, reasons,
                        {"track_id": person.track_id, "prone_seconds": round(timestamp - state["prone_since"], 2),
                         "stationary_seconds": round(still, 2), "centroid_speed_px_s": round(speed, 2)},
                        (person.track_id, person.track_id), True, event_type)
                    events.append(signal)
                    state["reported"] = event_type
                    state["assessment"] = replace(signal, trigger=False, events=[])
                if state.get("assessment"):
                    current_postures.append(state["assessment"])
            else:
                for key in ("prone_since", "stationary_since", "reported", "assessment", "fall"):
                    state.pop(key, None)
                if timestamp - state.get("upright", (-100, 0, 1))[0] > 2:
                    state.pop("upright", None)
            self.tracks[person.track_id] = state
        self.tracks = {key: value for key, value in self.tracks.items() if key in active}
        self.hands = {key: value for key, value in self.hands.items() if key in active}
        priority = {"hands_up": 0, "fight": 2, "person_down": 1, "possible_fall": 1, "person_down_after_fight": 3}
        choices = ([result] if result.state == "possible_fight" else []) + current_postures
        if choices:
            choice = max(choices, key=lambda item: priority[item.event_type])
            result = replace(choice, trigger=any(event.event_type == choice.event_type and event.pair == choice.pair for event in events))
        result.events = [replace(event, events=[]) for event in events]
        result.signals = {**result.signals, "pending_pairs": pending_pairs}
        return result
