"""Observed pipeline health and cautious capacity planning."""
import math
import os
import platform
import shutil
import subprocess
import time
from functools import lru_cache


SERVICE_STARTED = time.monotonic()

PRICE_REFERENCE = {
    "name": "Illustrative analysis-computer allowance",
    "inr": 100000,
    "as_of": "2026-09-18",
    "url": None,
    "note": "Budget assumption only. Camera throughput needs a full-pipeline benchmark.",
}


@lru_cache(maxsize=1)
def hardware_snapshot():
    """Expose only useful specifications; never expose machine identifiers."""
    system = platform.system()
    result = {"os": f"{system} {platform.release()}", "architecture": platform.machine(),
              "cpu_cores": os.cpu_count(), "chip": None, "model": None, "gpu": None,
              "memory_gb": None, "inference_device": "CPU" if system == "Darwin" else "CPU or CUDA (selected at runtime)"}
    if system == "Darwin":
        try:
            output = subprocess.run(["/usr/sbin/system_profiler", "SPHardwareDataType"],
                                    capture_output=True, text=True, timeout=5, check=True).stdout
            for line in output.splitlines():
                key, _, value = line.strip().partition(": ")
                if key == "Model Name":
                    result["model"] = value
                elif key == "Chip":
                    result["chip"] = value
                elif key == "Memory":
                    amount = value.split()[0]
                    try:
                        result["memory_gb"] = float(amount)
                    except ValueError:
                        pass
        except (OSError, subprocess.SubprocessError):
            pass
        try:
            output = subprocess.run(["/usr/sbin/system_profiler", "SPDisplaysDataType"],
                                    capture_output=True, text=True, timeout=5, check=True).stdout
            for line in output.splitlines():
                if line.strip().startswith("Chipset Model: "):
                    result["gpu"] = line.partition(": ")[2].strip()
                    break
        except (OSError, subprocess.SubprocessError):
            pass
    else:
        try:
            result["memory_gb"] = round(os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE") / 1024**3, 1)
        except (ValueError, OSError, AttributeError):
            pass
        try:
            with open("/proc/cpuinfo", encoding="utf-8") as info:
                for line in info:
                    if line.startswith("model name"):
                        result["chip"] = line.partition(":")[2].strip()
                        break
        except OSError:
            pass
        try:
            output = subprocess.run(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
                                    capture_output=True, text=True, timeout=2, check=True).stdout
            result["gpu"] = output.splitlines()[0].strip() if output.strip() else None
        except (OSError, subprocess.SubprocessError):
            pass
    return result


def source_health(sources):
    rows = []
    for source in sources:
        status = source.get("status")
        completed = status == "finished"
        input_fps = float((source.get("source_fps") if completed else source.get("capture_fps")) or 0)
        target = float(source.get("target_fps") or 20)
        eco_quiet = bool(source.get("eco_mode") and source.get("eco_state") == "quiet")
        requested = min(target, 1.0) if eco_quiet else target
        expected = min(requested, input_fps) if input_fps else requested
        analyzed = float((source.get("last_processed_fps") if completed else source.get("processed_fps")) or 0)
        eco_active_seconds = float(source.get("eco_active_seconds") or 0)
        eco_quiet_seconds = float(source.get("eco_quiet_seconds") or 0)
        eco_active_scans = int(source.get("eco_active_pose_frames") or 0)
        eco_quiet_scans = int(source.get("eco_quiet_pose_frames") or 0)
        eco_comparison_ready = eco_active_seconds >= 2 and eco_active_scans >= 2
        eco_active_rate = min(target, eco_active_scans / eco_active_seconds) if eco_comparison_ready else None
        eco_normal_estimate = (eco_active_scans + eco_active_rate * eco_quiet_seconds
                               if eco_active_rate is not None else None)
        eco_actual_scans = eco_active_scans + eco_quiet_scans
        eco_saved_estimate = max(0, eco_normal_estimate - eco_actual_scans) if eco_normal_estimate is not None else None
        eco_reduction = (100 * eco_saved_estimate / eco_normal_estimate
                         if eco_normal_estimate and eco_saved_estimate is not None else 0)
        measured = status in {"running", "finished"} and source.get("processed_frames", 0) >= 30 and expected > 0 and analyzed > 0
        keeping_up = measured and analyzed >= expected * .9 and not source.get("stale")
        rows.append({"camera_id": source.get("camera_id"), "name": source.get("name"), "status": status,
                     "source_kind": source.get("source_kind"), "stale": bool(source.get("stale")),
                     "workers_stale": bool(source.get("threat_stale") or (source.get("depth_meta") or {}).get("stale")),
                     "storage_error": bool(source.get("storage_error")), "input_fps": round(input_fps, 1),
                     "target_fps": round(target, 1), "analyzed_fps": round(analyzed, 1),
                     "rate_is_historical": completed,
                     "skipped_frames": int(source.get("skipped_frames") or 0),
                     "processed_frames": int(source.get("processed_frames") or 0),
                     "frame_age_seconds": source.get("frame_age_seconds"),
                     "dropped_records": int(source.get("dropped_records") or 0),
                     "frame_work_ms": source.get("latency_ms"), "depth_status": source.get("depth_status", "off"),
                     "object_status": source.get("threat_status", "off"), "keeping_up": keeping_up,
                     "measured": measured, "eco_mode": bool(source.get("eco_mode")),
                     "eco_state": source.get("eco_state", "off"),
                     "eco_motion_score": source.get("eco_motion_score", 0),
                     "eco_skipped_frames": int(source.get("eco_skipped_frames") or 0),
                     "eco_active_seconds": round(eco_active_seconds, 1),
                     "eco_quiet_seconds": round(eco_quiet_seconds, 1),
                     "eco_comparison_ready": eco_comparison_ready,
                     "eco_active_pose_fps": round(eco_active_rate, 1) if eco_active_rate is not None else None,
                     "eco_normal_pose_scans_estimate": round(eco_normal_estimate) if eco_normal_estimate is not None else None,
                     "eco_actual_pose_scans": eco_actual_scans,
                     "eco_pose_scans_saved_estimate": round(eco_saved_estimate) if eco_saved_estimate is not None else None,
                     "eco_pose_reduction_pct": round(eco_reduction, 1)})
    return rows


def capacity_report(sources, disk_path="."):
    rows = source_health(sources)
    running = [row for row in rows if row["status"] == "running"]
    # Only a run without concurrent sources can calibrate one-camera throughput.
    single_source = (rows[0] if len(rows) == 1 and rows[0]["measured"]
                     and not (rows[0]["eco_mode"] and rows[0]["eco_state"] == "quiet") else None)
    baseline = single_source["analyzed_fps"] if single_source else None
    baseline_source = ("completed_video" if single_source["rate_is_historical"] else "live_source") if single_source else None
    if not running and any(row["rate_is_historical"] and row["measured"] for row in rows):
        verdict = "completed"
    elif not running:
        verdict = "no_live_measurement"
    elif any(not row["measured"] for row in running):
        verdict = "warming_up"
    elif any(not row["keeping_up"] for row in running):
        verdict = "behind"
    elif any(row["depth_status"] != "ready" or row["object_status"] != "ready" for row in running):
        verdict = "partial"
    else:
        verdict = "keeping_up"
    disk = shutil.disk_usage(disk_path)
    disk_free_gb = round(disk.free / 1024**3, 1)
    cameras = [row for row in rows if row["source_kind"] != "upload"]
    online = [row for row in cameras if row["status"] == "running" and not row["stale"]]
    issues = []
    for row in cameras:
        name = row["name"] or "Camera"
        if row["status"] == "error" or row["stale"]:
            issues.append(f"{name}: no live video. Check camera power and connection.")
        elif row["status"] in {"idle", "finished"}:
            issues.append(f"{name}: analysis stopped. Open Sources to reconnect.")
        elif row["status"] == "running" and row["measured"] and not row["keeping_up"]:
            issues.append(f"{name}: analysis is falling behind.")
        if row["status"] == "running" and (row["depth_status"] != "ready" or row["object_status"] != "ready" or row["workers_stale"]):
            issues.append(f"{name}: depth or object checks are not ready or are delayed.")
    if any(row["storage_error"] for row in rows):
        issues.append("Clips or event records could not be saved. Check device storage.")
    low_storage = disk.free < max(5 * 1024**3, disk.total * .1)
    if low_storage:
        issues.append("Storage is nearly full. Review saved videos and clip cleanup.")
    device_status = ("attention" if issues else "idle" if not cameras
                     else "starting" if any(not row["measured"] or row["status"] == "starting" for row in cameras)
                     else "ready")
    eco_compared = [row for row in cameras if row["eco_comparison_ready"]]
    eco_normal_total = sum(row["eco_normal_pose_scans_estimate"] for row in eco_compared)
    eco_actual_total = sum(row["eco_actual_pose_scans"] for row in eco_compared)
    eco_saved_total = max(0, eco_normal_total - eco_actual_total)
    device = {"schema_version": 2, "service_uptime_seconds": int(time.monotonic() - SERVICE_STARTED),
              "checked_at": time.time(),
              "healthy_cameras": sum(row["measured"] and row["keeping_up"] and
                                     row["depth_status"] == "ready" and row["object_status"] == "ready"
                                     and not row["workers_stale"] for row in online),
              "status": device_status, "cameras": cameras, "online_cameras": len(online),
              "configured_cameras": len(cameras), "issues": issues,
              "eco_cameras": sum(row["eco_mode"] for row in cameras),
              "eco_quiet_cameras": sum(row["eco_mode"] and row["eco_state"] == "quiet" for row in cameras),
              "eco_skipped_frames": sum(row["eco_skipped_frames"] for row in cameras),
              "eco_active_seconds": round(sum(row["eco_active_seconds"] for row in cameras), 1),
              "eco_quiet_seconds": round(sum(row["eco_quiet_seconds"] for row in cameras), 1),
              "eco_comparison": {"ready": bool(eco_compared), "camera_count": len(eco_compared),
                                 "normal_pose_scans_estimate": eco_normal_total,
                                 "actual_pose_scans": eco_actual_total,
                                 "pose_scans_saved_estimate": eco_saved_total,
                                 "pose_reduction_pct": round(100 * eco_saved_total / eco_normal_total, 1) if eco_normal_total else 0},
              "storage": {"total_gb": round(disk.total / 1024**3, 1),
                          "used_gb": round(disk.used / 1024**3, 1), "free_gb": disk_free_gb,
                          "used_pct": round(disk.used / disk.total * 100, 1),
                          "low": low_storage}}
    return {"device": device, "hardware": hardware_snapshot(), "sources": rows, "verdict": verdict,
            "baseline_fps": baseline, "baseline_source": baseline_source, "disk_free_gb": disk_free_gb,
            "source_limit_per_process": 4, "price_reference": PRICE_REFERENCE}


def estimate_capacity(data):
    """Estimate current-machine equivalents, not untested hardware performance."""
    try:
        cameras = int(data["cameras"])
        target_fps = float(data["target_fps"])
        source_fps = float(data["source_fps"])
        baseline_fps = float(data["baseline_fps"])
        headroom_pct = float(data.get("headroom_pct", 25))
        bitrate_mbps = float(data.get("bitrate_mbps", 4))
        retention_days = float(data.get("retention_days", 7))
        unit_cost_inr = float(data.get("unit_cost_inr", PRICE_REFERENCE["inr"]))
        camera_cost_inr = float(data.get("camera_cost_inr", 0))
    except (KeyError, ValueError, TypeError):
        raise ValueError("Enter valid numbers for all planning inputs.") from None
    if not (1 <= cameras <= 128 and 1 <= target_fps <= 60 and 1 <= source_fps <= 120
            and 1 <= baseline_fps <= 240 and 0 <= headroom_pct <= 60
            and 0 < bitrate_mbps <= 100 and 0 <= retention_days <= 365
            and 0 <= unit_cost_inr <= 100000000 and 0 <= camera_cost_inr <= 100000000):
        raise ValueError("One or more planning inputs are outside the supported range.")
    analyzed_per_camera = min(target_fps, source_fps)
    effective_capacity = baseline_fps * (1 - headroom_pct / 100)
    process_units = math.ceil(cameras / 4)
    single_camera_ok = analyzed_per_camera <= effective_capacity
    cameras_per_unit = min(4, math.floor(effective_capacity / analyzed_per_camera)) if single_camera_ok else 0
    units = math.ceil(cameras / cameras_per_unit) if cameras_per_unit else None
    storage_gb = cameras * bitrate_mbps * 1_000_000 / 8 * 86400 * retention_days / 1_000_000_000
    return {"cameras": cameras, "analyzed_fps_per_camera": round(analyzed_per_camera, 1),
            "total_analyzed_fps": round(cameras * analyzed_per_camera, 1),
            "total_input_fps": round(cameras * source_fps, 1),
            "effective_fps_per_unit": round(effective_capacity, 1),
            "current_machine_equivalents": units, "single_camera_fits": single_camera_ok,
            "cameras_per_unit": cameras_per_unit,
            "required_one_camera_baseline_fps": round(analyzed_per_camera / (1 - headroom_pct / 100), 1),
            "minimum_processes": process_units,
            "machine_cost_inr": round(units * unit_cost_inr) if units is not None else None,
            "camera_cost_inr": round(cameras * camera_cost_inr),
            "estimated_purchase_inr": round(units * unit_cost_inr + cameras * camera_cost_inr) if units is not None else None,
            "recording_storage_gb": round(storage_gb, 1),
            "price_is_reference": unit_cost_inr == PRICE_REFERENCE["inr"]}
