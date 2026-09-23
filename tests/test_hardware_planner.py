"""Capacity planning must expose assumptions and avoid claiming unmeasured FPS."""
import pytest

from vmd.app import create_app
from vmd.hardware_planner import plan_hardware


INPUT = {"cameras": 1, "cameras_per_computer": 2, "target_fps": 15,
         "source_fps": 25, "budget_inr": 100000, "camera_cost_inr": 0,
         "resolution": "720p", "bitrate_mbps": 12,
         "events_per_camera_day": 5, "clip_seconds": 15,
         "retention_days": 7, "headroom_pct": 25}


def test_one_camera_shows_starting_specs_current_machine_and_rough_cost():
    host = {"model": "MacBook Air", "chip": "Apple M5", "cpu_cores": 10,
            "memory_gb": 16, "gpu": "Apple M5", "inference_device": "CPU"}
    plan = plan_hardware(INPUT, measured_fps=15, current_free_gb=376,
                         current_hardware=host, measurement_source="completed_video")
    assert plan["planned_computers"] == 1
    assert plan["software_instances"] == 1
    assert plan["one_camera_benchmark_fps"] == 20
    assert plan["busiest_computer_benchmark_fps"] == 20
    assert plan["requirements"]["cpu_logical_cores_start"] == 6
    assert plan["requirements"]["ram_gb_start"] == 16
    assert plan["requirements"]["gpu_advised"] is False
    assert plan["requirements"]["local_storage_gb_per_computer_start"] == 256
    assert plan["storage"]["evidence_gb_total"] == 1.6
    assert plan["storage"]["added_2tb_drives"] == 0
    assert plan["storage"]["mode"] == "event_clips_only"
    assert not plan["storage"]["continuous_recording_is_implemented"]
    assert plan["cost"]["total_low_inr"] == 70000
    assert plan["cost"]["total_high_inr"] == 150000
    assert plan["cost"]["budget_position"] == "in_range"
    assert plan["current_machine"]["hardware"] == host
    assert plan["current_machine"]["ram_fits_start"] is True
    assert plan["current_machine"]["disk_fits_evidence"] is True
    assert plan["current_machine"]["observed_one_camera_meets_target"] is True
    assert plan["current_machine"]["measurement_source"] == "completed_video"
    assert "products" not in plan


def test_camera_grouping_and_retention_change_size_and_cost_without_fps_claims():
    two = plan_hardware({**INPUT, "cameras": 2, "camera_cost_inr": 900})
    assert two["planned_computers"] == 1
    assert two["requirements"]["gpu_advised"] is True
    assert two["storage"]["evidence_gb_total"] == 3.3
    assert two["cost"]["camera_inr"] == 1800
    assert two["cost"]["total_low_inr"] == 71800

    five = plan_hardware({**INPUT, "cameras": 5, "cameras_per_computer": 4})
    assert five["planned_computers"] == 2
    assert five["software_instances"] == 2
    assert five["requirements"]["ram_gb_start"] == 32
    assert five["requirements"]["cpu_logical_cores_start"] == 8
    assert five["busiest_computer_benchmark_fps"] == 80
    assert five["cost"]["compute_low_inr"] == 200000
    assert five["cost"]["compute_high_inr"] == 440000

    spread = plan_hardware({**INPUT, "cameras": 5, "cameras_per_computer": 1})
    assert spread["planned_computers"] == 5
    assert spread["cost"]["compute_low_inr"] > five["cost"]["compute_low_inr"]
    assert spread["software_instances"] == five["software_instances"]

    faster = plan_hardware({**INPUT, "target_fps": 30, "source_fps": 30})
    assert faster["requirements"]["ram_gb_start"] == 32
    assert faster["requirements"]["gpu_advised"] is True
    assert faster["cost"]["total_low_inr"] > plan_hardware(INPUT)["cost"]["total_low_inr"]

    retained = plan_hardware({**INPUT, "cameras": 4, "cameras_per_computer": 4,
                              "events_per_camera_day": 1000, "clip_seconds": 120,
                              "bitrate_mbps": 100, "retention_days": 365})
    assert retained["storage"]["evidence_gb_total"] > five["storage"]["evidence_gb_total"]
    assert retained["storage"]["added_2tb_drives"] > 0
    assert retained["cost"]["storage_low_inr"] > 0
    assert retained["requirements"]["local_storage_gb_per_computer_start"] > 256


@pytest.mark.parametrize("change", [
    {"cameras": 0}, {"cameras_per_computer": 5}, {"budget_inr": "nan"},
    {"camera_cost_inr": -1}, {"resolution": "8k"},
])
def test_bad_inputs_are_rejected(change):
    with pytest.raises(ValueError):
        plan_hardware({**INPUT, **change})


def test_legacy_plan_api_and_device_status_page(tmp_path):
    app = create_app(tmp_path)
    client = app.test_client()
    try:
        assert client.post('/api/capacity/plan', json=INPUT).status_code == 403
        response = client.post('/api/capacity/plan', json=INPUT,
                               headers={'X-VMD-Client': 'dashboard'})
        assert response.status_code == 200
        assert response.json['requirements']['ram_gb_start'] == 16
        assert response.json['current_machine']['hardware']['architecture']
        assert response.json['cost']['total_low_inr'] == 70000
        assert response.json['price_basis'][0]['url'].startswith('https://')
        assert 'products' not in response.json
        page = client.get('/')
        assert page.status_code == 200
        assert b'Is everything working?' in page.data
        assert b'How long should new clips stay?' in page.data
        assert b'id="device-camera-count"' in page.data
        assert b'id="device-disk-meter"' in page.data
        assert b'id="capacity-form"' not in page.data
        assert b'name="budget_inr"' not in page.data
        assert b'id="retention-form"' in page.data
    finally:
        app.extensions['vmd_manager'].close()
