"""Explain planning specifications and indicative costs without recommending a product.

RAM, CPU and price bands are starting assumptions, not measured VMD capacity.
The operator chooses how many cameras to budget per computer; that number must
be validated with the full pipeline before a purchase or deployment.
"""
import math


CATALOG_DATE = "2026-09-18"
PRICE_BASIS = (
    {"label": "Small enclosed edge computer price example", "url": "https://thinkrobotics.com/products/nvidia-jetson-orin-super-nano-deployment-kit-made-in-india"},
    {"label": "Higher-memory edge computer price example", "url": "https://robu.in/product/avermedia-d133oxb-standard-box-pc-with-nvidia-jetson-orin-nx-16gb-module/"},
    {"label": "2 TB external storage price example", "url": "https://www.reliancedigital.in/product/seagate-2-tb-basic-portable-external-hard-disk-drive-hdd-lbxthh"},
)
EVIDENCE_BITRATES_MBPS = {"720p": 12, "1080p": 24, "4k": 48}
NORMAL_SAMPLES_PER_DAY = 24
NORMAL_SAMPLE_SECONDS = 10
NORMAL_SAMPLE_BITRATE_MBPS = 4
SOURCE_LIMIT_PER_PROCESS = 4
BASE_SSD_GB = 256
EXTRA_DRIVE_GB = 2000


def _valid_number(data, key, minimum, maximum, *, integer=False, default=None):
    try:
        raw = data[key] if default is None else data.get(key, default)
        value = int(raw) if integer else float(raw)
        if integer and str(raw).strip() != str(value):
            raise ValueError
    except (KeyError, TypeError, ValueError):
        raise ValueError(f"Enter a valid {key.replace('_', ' ')}.") from None
    if not math.isfinite(value) or not minimum <= value <= maximum:
        raise ValueError(f"{key.replace('_', ' ').capitalize()} must be between {minimum} and {maximum}.")
    return value


def _round_up(value, step):
    return int(math.ceil(value / step) * step)


def plan_hardware(data, measured_fps=None, current_free_gb=None, current_hardware=None,
                  measurement_source=None):
    cameras = _valid_number(data, "cameras", 1, 128, integer=True)
    target_fps = _valid_number(data, "target_fps", 1, 60)
    source_fps = _valid_number(data, "source_fps", 1, 120)
    cameras_per_computer = _valid_number(data, "cameras_per_computer", 1, 4, integer=True, default=2)
    budget = _valid_number(data, "budget_inr", 0, 100000000)
    camera_price = _valid_number(data, "camera_cost_inr", 0, 10000000, default=0)
    resolution = data.get("resolution", "720p")
    if resolution not in EVIDENCE_BITRATES_MBPS:
        raise ValueError("Choose 720p, 1080p or 4k evidence resolution.")
    bitrate = _valid_number(data, "bitrate_mbps", .1, 100, default=EVIDENCE_BITRATES_MBPS[resolution])
    retention = _valid_number(data, "retention_days", 1, 365, default=7)
    events_per_day = _valid_number(data, "events_per_camera_day", 1, 1000, default=5)
    clip_seconds = _valid_number(data, "clip_seconds", 1, 120, default=15)
    headroom = _valid_number(data, "headroom_pct", 0, 60, default=25)

    analyzed_fps = min(target_fps, source_fps)
    planned_computers = math.ceil(cameras / cameras_per_computer)
    busiest_computer_cameras = min(cameras_per_computer, cameras)
    per_camera_incident_gb_day = events_per_day * clip_seconds * bitrate / 8000
    per_camera_normal_gb_day = (NORMAL_SAMPLES_PER_DAY * NORMAL_SAMPLE_SECONDS
                                * NORMAL_SAMPLE_BITRATE_MBPS / 8000)
    evidence_gb_day = cameras * (per_camera_incident_gb_day + per_camera_normal_gb_day)
    evidence_gb = evidence_gb_day * retention
    busiest_evidence_gb = busiest_computer_cameras * evidence_gb / cameras
    # 64 GB for OS, models and app; 25% free space beyond the evidence estimate.
    ssd_gb = max(BASE_SSD_GB, _round_up(64 + busiest_evidence_gb / .75, 128))
    added_drives_per_computer = math.ceil(max(0, ssd_gb - BASE_SSD_GB) / EXTRA_DRIVE_GB)
    added_drives = planned_computers * added_drives_per_computer

    # Starting specifications, not measured requirements or throughput guarantees.
    larger_tier = busiest_computer_cameras > 2 or analyzed_fps > 15
    ram_gb = 32 if larger_tier else 16
    cpu_cores = 8 if larger_tier else 6
    gpu_advised = busiest_computer_cameras > 1 or analyzed_fps > 15
    cost_per_computer_low = 70000 if ram_gb == 16 else 100000
    cost_per_computer_high = 150000 if ram_gb == 16 else 220000
    compute_low = planned_computers * cost_per_computer_low
    compute_high = planned_computers * cost_per_computer_high
    storage_low = added_drives * 6000
    storage_high = added_drives * 10000
    camera_cost = round(cameras * camera_price)
    total_low = compute_low + storage_low + camera_cost
    total_high = compute_high + storage_high + camera_cost

    one_camera_benchmark_fps = analyzed_fps / (1 - headroom / 100)
    busiest_benchmark_fps = busiest_computer_cameras * one_camera_benchmark_fps
    total_benchmark_fps = cameras * one_camera_benchmark_fps
    measured = float(measured_fps) if measured_fps is not None else None
    if measured is not None and (not math.isfinite(measured) or measured <= 0):
        measured = None
    hardware = current_hardware or {}
    free_gb = max(0, float(current_free_gb)) if current_free_gb is not None else None
    current = {
        "hardware": hardware,
        "free_disk_gb": round(free_gb, 1) if free_gb is not None else None,
        "ram_fits_start": hardware.get("memory_gb") >= ram_gb if isinstance(hardware.get("memory_gb"), (int, float)) else None,
        "cpu_fits_start": hardware.get("cpu_cores") >= cpu_cores if isinstance(hardware.get("cpu_cores"), int) else None,
        "disk_fits_evidence": free_gb >= evidence_gb / .75 if free_gb is not None else None,
        "observed_one_camera_fps": round(measured, 1) if measured is not None else None,
        "observed_one_camera_meets_target": measured >= analyzed_fps * .9 if measured is not None else None,
        "measurement_source": measurement_source,
    }
    return {
        "as_of": CATALOG_DATE, "currency": "INR", "cameras": cameras,
        "budget_inr": round(budget), "camera_cost_inr": camera_cost,
        "analysis_fps_per_camera": round(analyzed_fps, 1),
        "camera_input_fps": source_fps,
        "planned_cameras_per_computer": cameras_per_computer,
        "planned_computers": planned_computers,
        "software_instances": math.ceil(cameras / SOURCE_LIMIT_PER_PROCESS),
        "headroom_pct": headroom,
        "one_camera_benchmark_fps": round(one_camera_benchmark_fps, 1),
        "busiest_computer_benchmark_fps": round(busiest_benchmark_fps, 1),
        "total_benchmark_fps": round(total_benchmark_fps, 1),
        "requirements": {
            "cpu_logical_cores_start": cpu_cores,
            "ram_gb_start": ram_gb,
            "gpu_advised": gpu_advised,
            "gpu_note": "A CUDA-capable GPU can help on Linux; the present macOS pipeline uses CPU. Measure the full pipeline before deciding whether a GPU is needed.",
            "local_storage_gb_per_computer_start": ssd_gb,
            "network": "Ethernet or USB camera input; IP cameras can use RTSP over Ethernet.",
            "display": "HDMI is optional and only needed for a local monitor. The browser dashboard does not need a video cable to the analysis computer.",
        },
        "storage": {
            "mode": "event_clips_only", "resolution": resolution,
            "bitrate_mbps": bitrate, "retention_days": retention,
            "events_per_camera_day": events_per_day, "clip_seconds": clip_seconds,
            "normal_samples_per_camera_day": NORMAL_SAMPLES_PER_DAY,
            "evidence_gb_per_day": round(evidence_gb_day, 2),
            "evidence_gb_total": round(evidence_gb, 1),
            "evidence_gb_on_busiest_computer": round(busiest_evidence_gb, 1),
            "added_2tb_drives": added_drives,
            "continuous_recording_is_implemented": False,
        },
        "cost": {
            "per_computer_low_inr": cost_per_computer_low,
            "per_computer_high_inr": cost_per_computer_high,
            "compute_low_inr": compute_low, "compute_high_inr": compute_high,
            "storage_low_inr": storage_low, "storage_high_inr": storage_high,
            "camera_inr": camera_cost,
            "total_low_inr": total_low, "total_high_inr": total_high,
            "budget_position": "below_range" if budget < total_low else "above_range" if budget > total_high else "in_range",
            "basis": "Illustrative Indian market allowance, not a bill of materials or VMD FPS guarantee.",
        },
        "current_machine": current,
        "price_basis": PRICE_BASIS,
    }
