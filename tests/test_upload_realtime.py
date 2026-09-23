"""Uploaded recordings advance through the same frame API used by the live UI."""
import io
import time

import cv2
import numpy as np

from vmd.app import create_app
import vmd.engine as engine_module
from vmd.heuristics import Person


HEADERS = {"X-VMD-Client": "dashboard"}


def test_uploaded_video_reports_progress_and_processed_frames(tmp_path, monkeypatch):
    source = tmp_path / "sample.avi"
    writer = cv2.VideoWriter(str(source), cv2.VideoWriter_fourcc(*"MJPG"), 12, (96, 64))
    assert writer.isOpened()
    for index in range(36):
        writer.write(np.full((64, 96, 3), index * 6, np.uint8))
    writer.release()

    class Pose:
        def __init__(self, *args): pass
        def infer(self, frame):
            points = [(0, 0, 0)] * 17
            for index, x, y in [(5, 20, 20), (6, 35, 20), (11, 20, 40), (12, 35, 40)]:
                points[index] = (x, y, .9)
            return [Person(7, (10, 10, 45, 55), points)]

    class Depth:
        def __init__(self, *args, **kwargs): self.polls = 0; self.last = None
        def start(self): pass
        def stop(self): pass
        def submit(self, sequence, source_time, frame):
            self.last = (sequence, source_time)
            return True
        def poll(self):
            self.polls += 1
            if self.polls <= 3 or self.last is None:
                return "ready", None, None
            depth = np.full((64, 96), .2, np.float32)
            depth[:, :48] = .8
            return "ready", {"jpeg": b"depth", "depth": depth, "sequence": self.last[0], "source_time": self.last[1],
                             "submitted_at": time.time(), "latency_ms": 0}, None

    monkeypatch.setattr(engine_module, "PoseModel", Pose)
    monkeypatch.setattr(engine_module, "THREAT_WEIGHTS", "test-no-threat-weights.pt")
    monkeypatch.setattr(engine_module, "DepthWorker", Depth)
    monkeypatch.setattr(engine_module, "choose_device", lambda requested: "cpu")
    app = create_app(tmp_path / "data")
    client = app.test_client()
    try:
        response = client.post("/api/sources", data={"kind": "upload", "video": (io.BytesIO(source.read_bytes()), "sample.avi")}, headers=HEADERS)
        assert response.status_code == 201
        camera_id = response.json["camera_id"]
        deadline = time.monotonic() + 4
        observed = None
        while time.monotonic() < deadline:
            observed = client.get("/api/sources").json["sources"][0]
            if observed["sequence"] >= 3 and observed.get("progress", 0) > 0:
                break
            time.sleep(.05)
        assert observed["source_kind"] == "upload"
        assert observed["duration_seconds"] == 3
        assert observed["sequence"] >= 3 and 0 < observed["progress"] <= 1
        assert observed["source_fps"] == 12
        assert observed["capture_fps"] > 0
        assert observed["processed_fps"] > 0
        assert observed["processed_frames"] + observed["skipped_frames"] == observed["sequence"]
        frames = client.get(f"/api/sources/{camera_id}/frames").json
        assert frames["pose"]
        while time.monotonic() < deadline + 4:
            observed = client.get("/api/sources").json["sources"][0]
            if observed["status"] == "finished":
                break
            time.sleep(.05)
        assert observed["status"] == "finished" and observed["progress"] == 1
        assert observed["captured_frames"] == 36
        assert observed["processed_frames"] + observed["skipped_frames"] == 36
        assert observed["last_processed_fps"] > 0
        capacity = client.get('/api/capacity').json
        assert capacity['verdict'] == 'completed'
        assert capacity['baseline_fps'] == observed['last_processed_fps']
        assert capacity['sources'][0]['analyzed_fps'] == observed['last_processed_fps']
        assert observed["depth_final_frame"] is True
        assert observed["depth_sequence"] == observed["sequence"] == 36
        assert observed["depth_people"] == [dict(track_id=7, relative_z=.2)]
        assert observed["depth_pair_sequence"] == 36
        frames = client.get(f"/api/sources/{camera_id}/frames").json
        assert frames["depth"] and not frames["depth_meta"]["stale"]
    finally:
        app.extensions["vmd_manager"].close()
