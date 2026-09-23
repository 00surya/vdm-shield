"""Compare tracked people within one inverse-depth frame; never metric distance."""
from itertools import combinations
from math import hypot

import numpy as np


def box_overlap(first, second):
    """Fraction of the smaller box covered by the intersection."""
    areas = [(box[2]-box[0]) * (box[3]-box[1])
             if box[2] > box[0] and box[3] > box[1] else 0 for box in (first, second)]
    if not all(np.isfinite(box).all() for box in (first, second)) or min(areas) <= 0:
        return 0.0
    width = max(0, min(first[2], second[2]) - max(first[0], second[0]))
    height = max(0, min(first[3], second[3]) - max(first[1], second[1]))
    return width * height / min(areas)


def fight_depth_evidence(depth, people):
    """Conservative same-frame depth support, not a physical contact measurement."""
    if (depth.ndim != 2 or not depth.size or not np.isfinite(depth).all()
            or float(np.ptp(depth)) <= 1e-6):
        return []
    readings = {person.track_id: torso_depth(depth, person) for person in people}
    observations = []
    for a, b in combinations(people, 2):
        if min(a.track_id, b.track_id) < 0 or a.track_id == b.track_id:
            continue
        first, second = readings[a.track_id], readings[b.track_id]
        status, gap = "uncertain", None
        if first is not None and second is not None and box_overlap(a.box, b.box) >= .05:
            gap = abs(first[0] - second[0])
            uncertainty = 2 * (first[1] + second[1])
            # Similar relative Z only supports an interaction hypothesis. A noisy
            # torso or a flat map must never be interpreted as matching depth.
            if gap + uncertainty <= .12:
                status = "compatible"
            elif gap >= max(.18, 3 * (first[1] + second[1])):
                status = "separated"
        observations.append({"tracks": list(sorted((a.track_id, b.track_id))),
                             "status": status, "relative_gap": gap})
    return observations


def torso_depth(depth, person):
    """Use several confident torso joints to avoid a single occluded pixel."""
    if person.track_id < 0 or not person.pose_reliable:
        return None
    height, width = depth.shape
    radius = max(2, min(10, int(person.height * .02)))
    values = []
    for index in (5, 6, 11, 12):
        x, y, confidence = person.keypoints[index]
        if confidence < .55 or not 0 <= x < width or not 0 <= y < height:
            continue
        cx, cy = int(x), int(y)
        patch = depth[max(0, cy-radius):min(height, cy+radius+1),
                      max(0, cx-radius):min(width, cx+radius+1)]
        finite = patch[np.isfinite(patch)]
        if finite.size:
            values.append(float(np.median(finite)))
    if len(values) < 3:
        return None
    middle = float(np.median(values))
    spread = float(np.median(np.abs(np.asarray(values)-middle)))
    if spread > .12:
        return None
    return middle, spread


def person_depths(depth, people):
    """Z increases away from the camera, on this frame's 0–1 relative scale."""
    usable = (depth.ndim == 2 and depth.size > 0 and np.isfinite(depth).all()
              and float(np.ptp(depth)) > 1e-6)
    readings = []
    for person in people:
        if person.track_id < 0:
            continue
        reading = torso_depth(depth, person) if usable else None
        readings.append({"track_id": person.track_id,
                         "relative_z": round(float(np.clip(1-reading[0], 0, 1)), 2) if reading else None})
    return readings


def compare_people(depth, people):
    """Report depth only for close image pairs in this exact sampled frame."""
    readings = {person.track_id: torso_depth(depth, person) for person in people}
    pairs = []
    for a, b in combinations(people, 2):
        if a.track_id < 0 or b.track_id < 0 or a.track_id == b.track_id:
            continue
        scale = (a.height + b.height) / 2
        image_gap = hypot(a.center[0]-b.center[0], a.center[1]-b.center[1]) / scale
        x_overlap = max(0, min(a.box[2], b.box[2])-max(a.box[0], b.box[0]))
        y_overlap = max(0, min(a.box[3], b.box[3])-max(a.box[1], b.box[1]))
        overlap = x_overlap * y_overlap > .05 * min(
            (a.box[2]-a.box[0])*(a.box[3]-a.box[1]),
            (b.box[2]-b.box[0])*(b.box[3]-b.box[1]))
        if image_gap >= .85 and not overlap:
            continue
        first, second = readings[a.track_id], readings[b.track_id]
        pair = {"tracks": [a.track_id, b.track_id], "overlap": bool(overlap),
                "image_gap": round(image_gap, 2), "status": "uncertain",
                "relative_gap": None, "nearer_track": None}
        if first is not None and second is not None:
            gap = abs(first[0]-second[0])
            pair["relative_gap"] = round(gap, 2)
            if gap >= max(.18, 3*(first[1]+second[1])):
                pair["status"] = "separated"
                # The model outputs inverse relative depth: larger values are nearer.
                pair["nearer_track"] = a.track_id if first[0] > second[0] else b.track_id
        pairs.append(pair)
    return sorted(pairs, key=lambda pair: (not pair["overlap"], pair["image_gap"]))
