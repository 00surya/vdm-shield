"""Internal sizing against measured, concurrent-camera device profiles."""
import math


def deployment_plan(data, profiles):
    def number(key, low, high):
        value = float(data.get(key, 0))
        if not math.isfinite(value) or not low <= value <= high:
            raise ValueError(f"{key} must be between {low} and {high}.")
        return value
    cameras = number("cameras", 1, 128)
    if not cameras.is_integer():
        raise ValueError("Camera count must be a whole number.")
    fps = number("fps", 1, 60)
    resolution = data.get("resolution", "1080p")
    pipeline = data.get("pipeline", "default")
    matches = []
    for profile in profiles:
        # Capacity is measured with all checks enabled concurrently, not extrapolated
        # from a single stream or a processor's advertised specifications.
        if (profile.get("validated") is not True or profile.get("resolution") != resolution
                or profile.get("pipeline") != pipeline):
            continue
        count = int(profile["tested_cameras"])
        rate = float(profile["minimum_camera_fps"])
        if count < 1 or not math.isfinite(rate) or rate < fps:
            continue
        matches.append({"name": profile["name"], "specifications": profile["specifications"],
                        "devices": math.ceil(cameras / count), "cameras_per_device": count,
                        "measured_minimum_fps": rate, "benchmark_date": profile["benchmark_date"],
                        "report": profile["report"]})
    matches.sort(key=lambda item: (item["devices"], item["cameras_per_device"]))
    return {"cameras": int(cameras), "target_fps": fps, "matches": matches,
            "status": "validated_matches" if matches else "benchmark_required",
            "note": "Matches require the same pipeline, resolution and tested configuration. Validate the deployment's scenes and streams before committing."}
