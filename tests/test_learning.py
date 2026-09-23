import time

import cv2
import numpy as np
import pytest

from vmd.app import create_app
from vmd.learning import holdout_split, promotion_gate


HEADERS = {"X-VMD-Client": "dashboard"}


def frames(pattern, variant=0):
    result = []
    for index in range(5):
        image = np.full((64, 96, 3), (30 + variant * 5) if pattern == "normal" else (200 + variant * 5), np.uint8)
        if pattern != "normal":
            cv2.rectangle(image, (index * 8, 12), (index * 8 + 20, 42), (0, 0, 0), -1)
        result.append((index * .4, cv2.imencode(".jpg", image)[1].tobytes()))
    return result


def test_one_click_training_from_raw_clips_and_review_override(tmp_path, monkeypatch):
    import vmd.learning as module
    class FakeEncoder:
        def __init__(self, *args): pass
        def clip(self, path):
            cap = cv2.VideoCapture(str(path)); ok, image = cap.read(); cap.release()
            return np.full((16, 512), image.mean() / 255, np.float32)
    monkeypatch.setattr(module, 'Encoder', FakeEncoder)
    app = create_app(tmp_path)
    client = app.test_client()
    store = app.extensions["vmd_manager"].store
    try:
        for index in range(3):
            assert store.enqueue("normal_sample", ("camera", time.time(), frames("normal", index)))
        for index in range(2):
            event = {"id": f"fight-{index}", "created": time.time(), "mode": "live",
                     "score": .8, "reasons": ["fixture"], "signals": {"source_seconds": 3},
                     "event_type": "fight"}
            assert store.enqueue("incident", (event, frames("normal"), frames("fight", index)))
        store.jobs.join()
        assert not store.error
        incident = store.incidents()[0]
        assert incident["raw_clip"] and (store.raw_clips / incident["raw_clip"]).is_file()
        assert client.get(f"/api/incidents/{incident['id']}/raw").status_code == 200
        assert client.get("/api/training").json["counts"] == {"fight": 2, "normal": 3}
        audit = client.get("/api/training").json["audit"]
        assert audit["candidate_clips"] == 5
        assert audit["provisional_clips"] == 5
        assert audit["source_count"] == 2
        assert audit["training_ready"] is True
        assert audit["validation_ready"] is False
        assert client.post("/api/training/train", headers=HEADERS).status_code == 202
        deadline = time.monotonic() + 20
        while store.learner.snapshot()["state"] == "training" and time.monotonic() < deadline:
            time.sleep(.03)
        assert store.learner.snapshot()["state"] == "candidate"
        assert store.learner.snapshot()["evaluation"]["state"] == "unavailable"
        assert store.learner.predict(frames("fight")) is None
        assert store.learner.candidate.exists()
        assert not store.learner.path.exists()
        assert client.post(f"/api/incidents/{incident['id']}/label", json={"label": "robbery"}, headers=HEADERS).status_code == 200
        assert store.training_counts()["robbery"] == 1
        assert client.post(f"/api/incidents/{incident['id']}/review", json={"decision": "false_positive"}, headers=HEADERS).status_code == 200
        assert store.training_counts()["normal"] == 4
        normal_id = store.normal_samples()[0]["id"]
        assert client.post(f"/api/training/normal/{normal_id}/review", json={"decision": "rejected"}, headers=HEADERS).status_code == 200
        assert store.training_counts()["normal"] == 3
    finally:
        app.extensions["vmd_manager"].close()


@pytest.mark.parametrize('label', ['hands_up', 'fight', 'person_down_after_fight'])
def test_learned_prediction_cannot_bypass_fight_confirmation(tmp_path, monkeypatch, label):
    import threading
    from types import SimpleNamespace
    import vmd.engine as module
    from vmd.engine import Engine
    from vmd.storage import Store

    class Capture:
        def __init__(self, source):
            self.finished = False
            self.error = None
            self.stop_event = threading.Event()
        def start(self): pass
        def stop(self): pass
        def latest(self, after):
            return after + 1, (after + 1) * .1, np.zeros((64, 96, 3), np.uint8)

    class Pose:
        def __init__(self, *args): pass
        def infer(self, frame): return []

    class Depth:
        def __init__(self, *args, **kwargs): pass
        def start(self): pass
        def stop(self): pass
        def submit(self, *args): pass
        def poll(self): return "loading", None, None

    monkeypatch.setattr(module, "Capture", Capture)
    monkeypatch.setattr(module, "PoseModel", Pose)
    monkeypatch.setattr(module, "THREAT_WEIGHTS", "test-no-threat-weights.pt")
    monkeypatch.setattr(module, "DepthWorker", Depth)
    monkeypatch.setattr(module, "choose_device", lambda _: "cpu")
    store = Store(tmp_path)
    predictions = []
    def predict(frames, source_id):
        predictions.append(label)
        return {"label": label, "margin": .3}
    monkeypatch.setattr(store.learner, "predict", predict)
    engine = Engine(store)
    try:
        engine.start(SimpleNamespace(mode="live", source="fixture", kind="camera",
                                     threshold=.6, hold_seconds=.7, target_fps=60))
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            if store.incidents() or (label != 'hands_up' and len(predictions) >= 3):
                break
            time.sleep(.03)
        incidents = store.incidents()
        assert predictions
        if label != 'hands_up':
            assert incidents == []
            assert engine.snapshot().get('alert') is None
            return
        assert len(incidents) == 1
        assert incidents[0]["event_type"] == label
        assert incidents[0]["signals"]["model_generated"]
        assert "MODEL:" in engine.snapshot()["alert"]["label"]
        assert not store.training_examples()  # Model output never trains itself without review.
        assert store.training_audit()["excluded_model_generated"] == 1
    finally:
        engine.stop()
        store.close()


def test_source_holdout_and_promotion_gate():
    examples = []
    for source, count in [('a', 3), ('b', 3), ('c', 5)]:
        for label in ('normal', 'fight'):
            examples.extend(dict(camera_id=source, label=label, reviewed=True) for _ in range(count))
    train, test = holdout_split(examples)
    assert not {examples[i]['camera_id'] for i in train} & {examples[i]['camera_id'] for i in test}
    assert len(test) == 10
    assert holdout_split(examples[:5]) is None
    good = dict(state='measured', event_precision=.8, event_recall=.9)
    assert promotion_gate(good)[0]
    assert not promotion_gate(dict(state='unavailable'))[0]
    assert not promotion_gate({**good, 'event_recall': .5})[0]
    assert not promotion_gate(good, {'event_recall': .95, 'event_precision': .7})[0]


def test_temporal_head_trains_saves_and_reloads(tmp_path):
    from vmd.temporal import fit_head, predict_features, save_model, load_model, CONFIG
    features = [np.full((16, 512), (-1 if i < 4 else 1), np.float32) for i in range(8)]
    head = fit_head(features, [0]*4 + [1]*4, [3]*8, 2)
    labels = ['normal', 'fight']
    predictions = predict_features(head, labels, features)
    assert [p['label'] for p in predictions] == ['normal']*4 + ['fight']*4
    path = tmp_path / 'head.npz'
    save_model(path, head, {'config': CONFIG, 'labels': labels})
    restored, meta = load_model(path)
    assert predict_features(restored, labels, features) == predictions
    assert sum(p.numel() for p in head.parameters()) == 110976 + 65 * 2


def test_async_prediction_is_source_isolated_and_stale_results_rejected(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from concurrent.futures import Future
    from vmd.learning import Learner
    learner = Learner(SimpleNamespace(directory=tmp_path))
    learner.model = object(); learner.metadata = {'labels': ['normal', 'fight']}
    monkeypatch.setattr(learner, '_infer', lambda *args: None)
    future = Future(); future.set_result({'label': 'fight', 'margin': .9})
    learner.pending['camera-a'] = (future, time.monotonic(), 1.6, learner.model)
    try:
        assert learner.predict(frames('fight'), 'camera-b') is None
        assert learner.predict(frames('fight'), 'camera-a')['label'] == 'fight'
        learner.pending['camera-a'] = (future, time.monotonic()-10, 1.6, learner.model)
        assert learner.predict(frames('fight'), 'camera-a') is None
        learner.forget_source('camera-b')
        assert 'camera-b' not in learner.pending
    finally:
        learner.close()


def test_temporal_sampling_includes_clip_end():
    from vmd.temporal import sample_indices
    indices = sample_indices(100)
    assert len(indices) == 16 and indices[0] == 0 and indices[-1] == 99


def test_partial_class_candidate_cannot_forget_or_skip_class_validation():
    from vmd.learning import class_coverage_gate
    evaluation = {'per_label': [{'label': 'normal', 'clips': 5, 'correct': 5},
                                {'label': 'fight', 'clips': 5, 'correct': 4}]}
    assert class_coverage_gate(['normal', 'fight'], evaluation)[0]
    assert not class_coverage_gate(['normal', 'fight'], evaluation, ['normal', 'fight', 'possible_fall'])[0]
    assert not class_coverage_gate(['normal', 'fight', 'possible_fall'], evaluation)[0]
    evaluation['per_label'][1]['correct'] = 2
    assert not class_coverage_gate(['normal', 'fight'], evaluation)[0]
