"""Dimensionless heuristic signals. A score is not a probability of violence."""
from dataclasses import dataclass, field, replace
from collections import deque
from itertools import combinations
from math import hypot


@dataclass
class Person:
    track_id: int
    box: tuple[float, float, float, float]
    keypoints: list[tuple[float, float, float]]  # COCO x, y, confidence
    depth: float | None = None  # frame-normalized inverse depth, never metres
    limb_speeds: dict | None = None
    body_scale: float | None = None
    pose_reliable: bool = True
    local_flow: float | None = None
    limb_motion: dict | None = None  # measured pixel flow, body heights / second

    @property
    def height(self):
        return max(1.0, self.box[3] - self.box[1])

    @property
    def center(self):
        x1, y1, x2, y2 = self.box
        return ((x1 + x2) / 2, (y1 + y2) / 2)

    @property
    def scale(self):
        return self.body_scale or self.height


@dataclass
class Rules:
    threshold: float = 0.60
    hold_seconds: float = 0.7
    cooldown_seconds: float = 12.0
    max_gap_seconds: float = 0.65
    signal_grace_seconds: float = 0.25
    proximity_heights: float = 0.85
    wrist_speed: float = 0.85  # body heights per second, relative to torso
    flow_speed: float = 0.04  # frame diagonals per second, person-local regions
    camera_speed: float = 0.09
    keypoint_confidence: float = 0.45
    strike_window_seconds: float = 2.4
    strike_evidence_fraction: float = 0.6
    contact_margin: float = 0.12
    contact_motion_speed: float = 0.35  # body heights / second around the moving limb


@dataclass
class Assessment:
    score: float = 0.0
    state: str = "observing"
    reasons: list[str] = field(default_factory=list)
    signals: dict = field(default_factory=dict)
    pair: tuple[int, int] | None = None
    trigger: bool = False
    event_type: str = "fight"
    events: list = field(default_factory=list)
    candidates: list = field(default_factory=list)


def segment_intersects_box(start, end, box):
    """Test the observed hand path against a rectangle without extrapolating it."""
    lower, upper = 0.0, 1.0
    for axis in (0, 1):
        delta = end[axis] - start[axis]
        if abs(delta) < 1e-9:
            if not box[axis] <= start[axis] <= box[axis + 2]:
                return False
            continue
        entry = (box[axis] - start[axis]) / delta
        leave = (box[axis + 2] - start[axis]) / delta
        lower = max(lower, min(entry, leave))
        upper = min(upper, max(entry, leave))
        if lower > upper:
            return False
    return True


class FightHeuristic:
    def __init__(self, rules=None):
        self.rules = rules or Rules()
        self.previous = {}
        self.last_time = None
        self.pending = {}
        self.last_positive = {}
        self.cooldowns = {}
        self.episodes = set()
        self.strike_history = {}

    def update(self, people, timestamp, local_flow=0.0, camera_motion=0.0):
        r = self.rules
        dt = timestamp - self.last_time if self.last_time is not None else 0
        valid_time = 0 < dt <= r.max_gap_seconds
        if not valid_time:
            self.previous.clear()
            self.pending.clear()
            self.last_positive.clear()
            self.episodes.clear()
            self.strike_history.clear()
        previous_people = self.previous
        wrist = {}
        for person in people:
            old = self.previous.get(person.track_id)
            speeds = []
            if valid_time and old:
                for index in (9, 10):
                    a, b = person.keypoints[index], old.keypoints[index]
                    if min(a[2], b[2]) >= r.keypoint_confidence:
                        # Subtract torso translation to avoid treating walking as punching.
                        c, d = person.center, old.center
                        speeds.append(hypot((a[0]-c[0])-(b[0]-d[0]), (a[1]-c[1])-(b[1]-d[1])) / (dt * person.height))
            wrist[person.track_id] = max(speeds, default=0)
            if person.limb_speeds is not None:
                wrist[person.track_id] = max((person.limb_speeds.get(i,0) for i in (9,10)),default=0)
        self.previous = {p.track_id: p for p in people}
        self.last_time = timestamp
        self.cooldowns = {k: v for k, v in self.cooldowns.items() if timestamp - v < r.cooldown_seconds}
        best = Assessment(signals={"people": len(people), "local_flow": round(local_flow, 3), "camera_motion": round(camera_motion, 3),
            "reliable_poses": sum(p.pose_reliable for p in people),
            "threshold": r.threshold, "required_seconds": r.hold_seconds,
            "blockers": ["Fight detection needs two visible tracked people"] if len(people)<2 else []})
        if camera_motion > r.camera_speed:
            self.pending.clear()
            self.last_positive.clear()
            self.episodes.clear()
            self.strike_history.clear()
            best.state = "camera_moving"
            best.reasons = ["Camera motion is too high; interaction alerts are paused"]
            best.signals["blockers"] = list(best.reasons)
            return best
        active_pairs = set()
        events = []
        candidates = []
        active_fights = []
        visible_pairs = set()
        for a, b in combinations(people, 2):
            if a.track_id < 0 or b.track_id < 0 or a.track_id == b.track_id:
                continue
            pair = tuple(sorted((a.track_id, b.track_id)))
            visible_pairs.add(pair)
            # Torso-derived scales can be only half the visible standing height.
            # Use the full visible body extent for pair distance, while retaining
            # torso-normalized limb speeds and the same narrow contact region.
            scale = (max(a.scale, a.height) + max(b.scale, b.height)) / 2
            distance = hypot(a.center[0]-b.center[0], a.center[1]-b.center[1]) / scale
            near = distance < r.proximity_heights
            speed = max(wrist[a.track_id], wrist[b.track_id])
            # Strong depth disagreement vetoes a pair; missing depth is explicitly neutral.
            depth_ok = a.depth is None or b.depth is None or abs(a.depth-b.depth) < 0.3
            proximity = max(0, 1 - distance / (r.proximity_heights * 1.5))
            limb = min(1, speed / r.wrist_speed)
            pair_flow = max(a.local_flow or 0,b.local_flow or 0) if a.local_flow is not None and b.local_flow is not None else local_flow
            motion = min(1, max(0, pair_flow) / r.flow_speed)
            # Include the observed limb path: a punch can land and retract between
            # processed frames. Require the same limb and consecutive track history.
            def reaches(actor, target, indices):
                x1,y1,x2,y2 = target.box
                margin = target.scale*r.contact_margin
                region = (x1-margin, y1-margin, x2+margin, y1+(y2-y1)*.72)
                old_actor = previous_people.get(actor.track_id) if valid_time else None
                old_target = previous_people.get(target.track_id) if valid_time else None
                for index in indices:
                    point = actor.keypoints[index]
                    if point[2] < r.keypoint_confidence:
                        continue
                    if region[0] <= point[0] <= region[2] and region[1] <= point[1] <= region[3]:
                        return True
                    if not old_actor or not old_target or not old_actor.pose_reliable or not old_target.pose_reliable:
                        continue
                    old_point = old_actor.keypoints[index]
                    if old_point[2] < r.keypoint_confidence:
                        continue
                    # Compare in the target's current frame of reference so simple
                    # shared translation cannot manufacture a crossing path.
                    start = tuple(old_point[axis] + target.center[axis] - old_target.center[axis] for axis in (0, 1))
                    if hypot(point[0]-start[0], point[1]-start[1]) <= actor.scale and segment_intersects_box(start, point, region):
                        return True
                return False
            hand_contact = reaches(a,b,(9,10)) or reaches(b,a,(9,10))
            foot_contact = reaches(a,b,(15,16)) or reaches(b,a,(15,16))
            kick_speed = max([p.limb_speeds.get(i,0) for p in (a,b) if p.limb_speeds is not None for i in (15,16)] or [0])
            if a.limb_speeds is not None and b.limb_speeds is not None:
                speed = max([actor.limb_speeds.get(i,0) for actor,target in ((a,b),(b,a)) for i in (9,10) if reaches(actor,target,(i,))] or [0])
                kick_speed = max([actor.limb_speeds.get(i,0) for actor,target in ((a,b),(b,a)) for i in (15,16) if reaches(actor,target,(i,))] or [0])
                limb = min(1,speed/r.wrist_speed)
            # Frame-normalized box flow can be tiny when only a fist moves. The
            # stabilizer already measured and validated flow around that exact limb.
            contact_motion = max([
                actor.limb_motion.get(index, 0)
                for actor, target in ((a, b), (b, a))
                if actor.limb_motion is not None and actor.limb_speeds is not None
                for index in (9, 10, 15, 16)
                if actor.limb_speeds.get(index, 0) >= r.wrist_speed * .7
                and reaches(actor, target, (index,))
            ] or [0])
            motion = min(1, max(motion, contact_motion / r.contact_motion_speed))
            pattern = "Striking arm movement" if hand_contact else "Close interaction"
            if foot_contact and kick_speed>speed:
                speed, limb, pattern = kick_speed,min(1,kick_speed/r.wrist_speed),"Fast kick toward another person's body"
            # Contact plus image-supported body displacement can also indicate a shove.
            # Opposing torso velocities distinguish this from people walking together.
            def torso_velocity(person):
                old = previous_people.get(person.track_id)
                if not valid_time or old is None or person.local_flow is None or person.local_flow < r.flow_speed*.5:
                    return (0,0)
                if any(min(person.keypoints[i][2],old.keypoints[i][2])<.55 for i in (5,6,11,12)):
                    return (0,0)
                return tuple(sum(person.keypoints[i][axis]-old.keypoints[i][axis] for i in (5,6,11,12))/(4*dt*scale) for axis in (0,1))
            va,vb=torso_velocity(a),torso_velocity(b)
            relative_body_speed=hypot(va[0]-vb[0],va[1]-vb[1])
            shove = hand_contact and speed>=r.wrist_speed*.35 and relative_body_speed>=.7 and motion>=1
            clinch = hand_contact and all(wrist[p.track_id]>=r.wrist_speed*.4 and (p.local_flow or 0)>=r.flow_speed for p in (a,b)) and distance<.5
            if shove or clinch:
                limb=max(limb,.85)
                pattern="Contact with rapid body displacement" if shove else "Sustained close-contact struggle motion"
            score = 0.25 * proximity + 0.45 * limb + 0.30 * motion if near and depth_ok else 0.0
            reasons = []
            if near:
                reasons.append("Two tracked people are close in the image")
            if speed >= r.wrist_speed or shove or clinch:
                reasons.append(pattern)
            if pair_flow >= r.flow_speed:
                reasons.append("High local motion around people")
            if contact_motion >= r.contact_motion_speed * .5:
                reasons.append("Image motion supports the same striking limb")
            if not depth_ok:
                reasons.append("Relative depth disagrees with proximity")
            # Require distinct evidence, sustained on the same tracked pair.
            candidate = valid_time and score >= r.threshold and (speed >= r.wrist_speed * .7 or shove or clinch) and motion >= .5 and (hand_contact or foot_contact) and a.pose_reliable and b.pose_reliable
            history = self.strike_history.setdefault(pair, deque())
            while history and timestamp-history[0][0]>max(r.strike_window_seconds,r.hold_seconds*2):
                history.popleft()
            if not near or not depth_ok:
                history.clear()
            if candidate and valid_time:
                history.append((timestamp, min(dt, .25)))
            supported_seconds = sum(sample[1] for sample in history)
            bursts = sum(i==0 or sample[0]-history[i-1][0]>r.signal_grace_seconds
                         for i,sample in enumerate(history))
            repeated = (len(history)>=4 and bursts>=2 and
                        history[-1][0]-history[0][0]>=r.hold_seconds and
                        supported_seconds>=max(.4,r.hold_seconds*r.strike_evidence_fraction))
            if candidate:
                active_pairs.add(pair)
                self.pending.setdefault(pair, timestamp)
                self.last_positive[pair] = timestamp
            elif near and depth_ok and timestamp-self.last_positive.get(pair, -1e10) <= r.signal_grace_seconds:
                # A punching wrist briefly slows at each reversal. Bridge only short gaps.
                active_pairs.add(pair)
            held = timestamp - self.pending.get(pair, timestamp)
            sustained = held >= r.hold_seconds or repeated
            trigger = candidate and sustained and pair not in self.cooldowns and pair not in self.episodes
            if trigger:
                self.cooldowns[pair] = timestamp
                self.episodes.add(pair)
            state = "possible_fight" if candidate and sustained else "evaluating" if candidate else "observing"
            blockers = []
            if not valid_time: blockers.append("Motion history is starting or the frame gap is too long")
            if not (a.pose_reliable and b.pose_reliable): blockers.append("Both people need a reliable upper-body pose")
            if not near: blockers.append("The tracked people are too far apart in the image")
            if not depth_ok: blockers.append("Same-frame relative depth disagrees with proximity")
            if motion < .5: blockers.append("Not enough image motion to support limb movement")
            if not (hand_contact or foot_contact): blockers.append("No limb reaches the other person's body region")
            if speed < r.wrist_speed*.7 and not (shove or clinch): blockers.append("No sufficiently fast supported strike")
            if score < r.threshold: blockers.append(f"Interaction score {score:.2f} is below the {r.threshold:.2f} threshold")
            if candidate and not sustained: blockers.append("Collecting repeated strikes or sustained interaction")
            if repeated: reasons.append("Repeated image-supported strike bursts within the temporal window")
            assessment = Assessment(round(score, 3), state, reasons, {
                    **best.signals, "proximity": round(proximity, 3), "wrist_speed": round(speed, 3),
                    "held_seconds": round(held, 2), "depth_available": a.depth is not None and b.depth is not None,
                    "pattern": pattern, "pair_flow": round(pair_flow,3), "relative_body_speed": round(relative_body_speed,3),
                    "strike_bursts": bursts, "supported_seconds": round(supported_seconds,2),
                    "contact_motion": round(contact_motion, 3),
                    "candidate": bool(candidate),
                    "blockers": blockers,
                }, pair, trigger)
            candidates.append(replace(assessment, events=[], candidates=[]))
            if trigger: events.append(replace(assessment, events=[]))
            if state=="possible_fight": active_fights.append(pair)
            if best.pair is None or (state=="possible_fight", score) > (best.state=="possible_fight", best.score) or trigger:
                best=assessment
        best.events=events
        best.candidates=candidates
        best.signals["active_fight_pairs"]=active_fights
        self.pending = {k: v for k, v in self.pending.items() if k in active_pairs}
        self.last_positive = {k: v for k, v in self.last_positive.items() if k in active_pairs}
        self.strike_history = {pair:history for pair,history in self.strike_history.items() if pair in visible_pairs and history}
        self.episodes.intersection_update(active_pairs | self.strike_history.keys())
        return best
