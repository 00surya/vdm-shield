from types import SimpleNamespace

from vmd.app import create_app
from vmd.models.sources import SourceManager, SourceSettings


class FakeEngine:
    def __init__(self, store, models, camera_id, name):
        self.camera_id, self.name = camera_id, name
        self.thread = None
        self.state = "idle"
        self.starts = 0

    def start(self, settings):
        self.settings = settings
        self.starts += 1
        self.state = "running"

    def stop(self):
        self.state = "idle"

    def snapshot(self):
        return {"status": self.state, "stale": False}


def test_camera_restore_stop_remove_and_retry(tmp_path, monkeypatch):
    monkeypatch.setattr("vmd.models.sources.Engine", FakeEngine)
    manager = SourceManager(tmp_path, tmp_path)
    manager.add(SourceSettings(source="0", name="Entrance"))
    camera_id = next(iter(manager.cameras))
    engine = manager.get(camera_id)
    engine.state = "error"
    manager.recover_once()
    assert engine.starts == 2
    engine.state = "error"
    manager.recover_once()
    assert engine.starts == 2  # retry backoff
    manager.set_enabled(camera_id, False)
    manager.close()
    restored = SourceManager(tmp_path, tmp_path)
    try:
        assert restored.get(camera_id).state == "idle"
        restored.recover_once()
        assert restored.get(camera_id).starts == 0
        restored.set_enabled(camera_id, True)
    finally:
        restored.close()
    running = SourceManager(tmp_path, tmp_path)
    try:
        assert running.get(camera_id).starts == 1
        running.remove(camera_id)
        assert not running.desired
    finally:
        running.close()


def test_password_protects_pages_apis_and_media(tmp_path):
    from werkzeug.security import generate_password_hash
    import base64
    (tmp_path / "access.hash").write_text(generate_password_hash("test-password-long"))
    app = create_app(tmp_path, tmp_path)
    try:
        client = app.test_client()
        for path in ("/", "/api/capacity", "/api/incidents/missing/clip"):
            assert client.get(path).status_code == 401
        credentials = base64.b64encode(b"operator:test-password-long").decode()
        response = client.get("/api/capacity", headers={"Authorization": "Basic " + credentials})
        assert response.status_code == 200
        assert response.json["device"]["product"]["access"] == "Password protected"
        assert response.json["device"]["evidence"]["writer_alive"]
        assert client.put("/api/evidence-retention", json={"days": 10},
                          headers={"Authorization": "Basic " + credentials}).status_code == 403
    finally:
        app.extensions["vmd_manager"].close()


def test_sizing_uses_concurrent_validated_benchmarks():
    from vmd.deployment import deployment_plan
    request = {"cameras": 5, "fps": 20, "resolution": "1080p", "pipeline": "default"}
    profile = {"validated": True, "name": "Fixture", "specifications": "Fixture specs",
               "tested_cameras": 4, "minimum_camera_fps": 21, "resolution": "1080p",
               "pipeline": "default", "benchmark_date": "fixture", "report": "fixture"}
    assert deployment_plan(request, [])["status"] == "benchmark_required"
    assert deployment_plan(request, [profile])["matches"][0]["devices"] == 2
    assert not deployment_plan({**request, "fps": 30}, [profile])["matches"]
    assert not deployment_plan(request, [{**profile, "validated": False}])["matches"]


def test_internal_sizing_hidden_on_customer_device(tmp_path, monkeypatch):
    monkeypatch.delenv("VMD_INTERNAL_TOOLS", raising=False)
    app = create_app(tmp_path, tmp_path)
    try:
        client = app.test_client()
        assert b'id="sizing-form"' not in client.get("/").data
        assert client.post("/api/deployment-plan", json={},
                           headers={"X-VMD-Client": "dashboard"}).status_code == 404
        monkeypatch.setenv("VMD_INTERNAL_TOOLS", "1")
        assert b'id="sizing-form"' in client.get("/").data
        result = client.post("/api/deployment-plan", json={"cameras": 4, "fps": 20},
                             headers={"X-VMD-Client": "dashboard"})
        assert result.json["status"] == "benchmark_required"
    finally:
        app.extensions["vmd_manager"].close()


def test_camera_fps_updates_live_settings_and_persists(tmp_path):
    import threading
    import json
    from dataclasses import asdict
    manager = SourceManager(tmp_path, tmp_path)
    try:
        settings = SourceSettings(source="0", target_fps=20)
        engine = SimpleNamespace(settings=settings, lock=threading.Lock(), state={"target_fps": 20})
        manager.cameras["fixture"] = engine
        manager.desired["fixture"] = {"settings": asdict(settings), "enabled": False}
        assert manager.set_target_fps("fixture", 12) == 12
        assert engine.settings.target_fps == engine.state["target_fps"] == 12
        assert json.loads(manager.saved_path.read_text())["fixture"]["settings"]["target_fps"] == 12
        import pytest
        for value in (None, True, 0, 61, 2.5, "20", float("inf")):
            with pytest.raises(ValueError):
                manager.set_target_fps("fixture", value)
        assert engine.settings.target_fps == 12
    finally:
        manager.cameras.clear()
        manager.close()


def test_camera_eco_mode_updates_live_settings_and_persists(tmp_path):
    import threading
    import json
    from dataclasses import asdict
    import pytest
    manager = SourceManager(tmp_path, tmp_path)
    try:
        settings = SourceSettings(source="0")
        engine = SimpleNamespace(settings=settings, lock=threading.Lock(), state={})
        manager.cameras["fixture"] = engine
        manager.desired["fixture"] = {"settings": asdict(settings), "enabled": False}
        assert manager.set_eco_mode("fixture", True) is True
        assert engine.settings.eco_mode is True
        assert engine.state["eco_mode"] is True
        assert engine.state["eco_state"] == "starting"
        assert json.loads(manager.saved_path.read_text())["fixture"]["settings"]["eco_mode"] is True
        assert manager.set_eco_mode("fixture", False) is False
        assert engine.state["eco_state"] == "off"
        with pytest.raises(ValueError):
            manager.set_eco_mode("fixture", "yes")
    finally:
        manager.cameras.clear()
        manager.close()
