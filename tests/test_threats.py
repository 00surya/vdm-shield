import io
import time

import cv2
import numpy as np
import pytest

from vmd.app import create_app
from vmd.threats import ThreatAlerts, WEIGHTS
import vmd.engine as engine_module


def test_object_alerts_are_immediate_and_cooldown_is_per_class():
    gate = ThreatAlerts()
    objects = [{"label": label, "confidence": .8, "box": [1, 2, 20, 30]}
               for label in ("gun", "knife", "grenade", "explosion", "bottle")]
    assert {item["event_type"] for item in gate.update(objects, 0)} == {
        "gun_detected", "knife_detected", "grenade_detected", "possible_explosion"}
    assert gate.update(objects, 1) == []
    assert len(gate.update(objects, 10)) == 4
    assert ThreatAlerts().update([{"label": "knife", "confidence": .4}], 0) == []


@pytest.mark.parametrize("detector_error", [False, True])
def test_threat_events_frames_and_detector_failure(tmp_path, monkeypatch, detector_error):
    source = tmp_path / "fixture.avi"
    writer = cv2.VideoWriter(str(source), cv2.VideoWriter_fourcc(*"MJPG"), 12, (96, 64))
    for _ in range(18):
        writer.write(np.zeros((64, 96, 3), np.uint8))
    writer.release()
    models = tmp_path / "models"
    models.mkdir()
    (models / WEIGHTS).write_bytes(b"mocked model")

    class Pose:
        def __init__(self, *args): pass
        def infer(self, frame): return []

    class Depth:
        def __init__(self, *args, **kwargs): pass
        def start(self): pass
        def stop(self): pass
        def submit(self, *args): pass
        def poll(self): return "error", None, "fixture depth disabled"

    class Threat:
        def __init__(self, *args):
            self.status = "error" if detector_error else "ready"
            self.result = None
        def start(self): pass
        def stop(self): pass
        def submit(self, sequence, timestamp, frame):
            _, jpeg = cv2.imencode(".jpg", frame)
            self.result = {"sequence": sequence, "source_time": timestamp,
                           "submitted_at": time.time(), "latency_ms": 1,
                           "jpeg": jpeg.tobytes(), "raw_jpeg": jpeg.tobytes(),
                           "detections": [{"label": "knife", "confidence": .9, "box": [10, 10, 30, 30]}] if sequence >= 3 else []}
        def poll(self): return self.status, self.result, "fixture failure" if detector_error else None

    monkeypatch.setattr(engine_module, "PoseModel", Pose)
    monkeypatch.setattr(engine_module, "DepthWorker", Depth)
    monkeypatch.setattr(engine_module, "ThreatWorker", Threat)
    monkeypatch.setattr(engine_module, "choose_device", lambda _: "cpu")
    app = create_app(tmp_path / "data", models)
    client = app.test_client()
    manager = app.extensions["vmd_manager"]
    try:
        response = client.post("/api/sources", headers={"X-VMD-Client": "dashboard"},
                               data={"kind": "upload", "video": (io.BytesIO(source.read_bytes()), "fixture.avi")})
        camera_id = response.json["camera_id"]
        deadline = time.monotonic() + 6
        while manager.get(camera_id).snapshot()["status"] not in {"finished", "error"} and time.monotonic() < deadline:
            time.sleep(.05)
        state = manager.get(camera_id).snapshot()
        assert state["status"] == "finished"
        manager.store.jobs.join()
        assert manager.store.error is None
        events = client.get("/api/incidents").json
        if detector_error:
            assert state["threat_status"] == "error"
            assert events == []
        else:
            assert len(events) == 1
            assert state["threat_sequence"] == state["sequence"]
            assert events[0]["event_type"] == "knife_detected"
            assert events[0]["signals"]["priority"] == "high"
            assert events[0]["raw_clip"]
            assert events[0]["evidence_start"] < events[0]["source_start"]
            assert events[0]["evidence_end"] > events[0]["source_end"]
            assert events[0]["raw_end"] > events[0]["source_end"]
            assert manager.store.training_examples() == []
            frames = client.get(f"/api/sources/{camera_id}/frames").json
            assert frames["threat"] and not frames["threat_stale"]
            unchanged = client.get(f"/api/sources/{camera_id}/frames?threat_after={frames['threat_sequence']}").json
            assert unchanged["threat"] is None
    finally:
        manager.close()


def test_general_objects_cannot_trigger_threat_alarms():
    gate = ThreatAlerts()
    objects = [{'label': label, 'confidence': .99, 'context_only': True}
               for label in ('knife', 'gun', 'person', 'car', 'scissors')]
    assert gate.update(objects, 0) == []
    assert gate.update([{'label': 'knife', 'confidence': .9}], 0)[0]['event_type'] == 'knife_detected'


def test_scene_objects_exposed_and_cleared_when_stale(tmp_path):
    from vmd.engine import Engine
    from vmd.storage import Store
    store = Store(tmp_path)
    engine = Engine(store)
    try:
        engine.state.update(status='running', threat_status='ready', threat_submitted_at=time.time(),
                            scene_objects=[{'label': 'car', 'context_only': True}], object_status='ready')
        assert engine.snapshot()['scene_objects'][0]['label'] == 'car'
        engine.state['threat_submitted_at'] = time.time() - 10
        assert engine.snapshot()['scene_objects'] == []
    finally:
        store.close()


def test_general_detector_failure_does_not_stop_weapon_worker(monkeypatch):
    import queue
    import threading
    import vmd.threats as module
    stop = threading.Event()
    requests, responses = queue.Queue(), queue.Queue()
    requests.put((1, 0., time.time(), np.zeros((48, 64, 3), np.uint8)))

    class Weapon:
        def __init__(self, *args): pass
        def infer(self, frame):
            stop.set()
            return [{'label': 'knife', 'confidence': .9, 'box': [1, 2, 20, 30]}]

    def unavailable(*args):
        raise RuntimeError('fixture general detector unavailable')

    monkeypatch.setattr(module, 'ThreatModel', Weapon)
    monkeypatch.setattr(module, 'ObjectModel', unavailable)
    module.run_threats(requests, responses, stop, 'cpu', 'models')
    packets = []
    while not responses.empty():
        packets.append(responses.get())
    sample = next(item for item in packets if 'jpeg' in item)
    assert sample['status'] == 'ready'
    assert sample['detections'][0]['label'] == 'knife'
    assert sample['object_status'] == 'error'
    assert sample['scene_objects'] == []
