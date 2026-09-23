"""Exercise the real engine, confirmation, event storage and asynchronous join."""
import threading
import time
from types import SimpleNamespace

import numpy as np
import pytest

import vmd.engine as module
from test_fight_confirmation import interacting_people, depth_map


@pytest.mark.parametrize('depth_case', ['matched', 'wrong_time', 'wrong_session', 'wrong_shape', 'separated'])
def test_live_engine_only_saves_fight_after_matching_depth_and_five_seconds(monkeypatch, depth_case):
    capture = None
    progress = []

    class Capture:
        session_id = 'camera-session'
        file = True
        def __init__(self, source):
            nonlocal capture
            capture = self
            self.sequence = 0
            self.finished = False
            self.error = None
            self.stop_event = threading.Event()
        def start(self): pass
        def stop(self): pass
        def identity(self, sequence):
            return {'session_id': self.session_id, 'frame_id': sequence}
        def latest(self, after):
            if after == -1:
                return self.packet
            if self.sequence >= 160:
                self.finished = True
                return None
            progress.append(dict(engine.state))
            self.sequence += 1
            self.packet = (self.sequence, self.sequence * .1, np.zeros((360, 440, 3), np.uint8))
            return self.packet

    class Pose:
        def __init__(self, *args): pass
        def infer(self, frame): return interacting_people()

    class Motion:
        def infer(self, *args): return .1, 0
        def person_flow(self, person): return .1
        def joint_motion(self, *args): return .8

    class Stabilizer:
        def update(self, people, *args): return people

    class Depth:
        def __init__(self, *args, **kwargs):
            self.status = 'ready'
            self.samples = []
            self.result = None
        def start(self): pass
        def stop(self): pass
        def submit(self, sequence, source_time, frame, priority=False):
            if not capture.finished and sequence % 5:
                return False
            if capture.finished:
                self.samples.clear()
            sample = {'sequence': sequence, 'source_time': source_time + (.01 if depth_case == 'wrong_time' else 0),
                      'depth': depth_map(depth_case == 'separated') if depth_case != 'wrong_shape' else np.zeros((2, 2)),
                      'identity': {'session_id': 'old-session' if depth_case == 'wrong_session' else capture.session_id},
                      'submitted_at': time.time(), 'latency_ms': 1, 'jpeg': b'depth'}
            # Results arrive after two newer pose frames; final draining is immediate.
            self.samples.append((sequence if capture.finished else sequence + 2, sample))
            return True
        def poll(self):
            while self.samples and self.samples[0][0] <= capture.sequence:
                self.result = self.samples.pop(0)[1]
            return 'ready', self.result, None

    class Store:
        error = None
        dropped = 0
        learner = SimpleNamespace(predict=lambda *args: None)
        def __init__(self): self.records = []
        def enqueue(self, kind, payload):
            self.records.append((kind, payload))
            return True

    monkeypatch.setattr(module, 'Capture', Capture)
    monkeypatch.setattr(module, 'PoseModel', Pose)
    monkeypatch.setattr(module, 'Motion', Motion)
    monkeypatch.setattr(module, 'PoseStabilizer', Stabilizer)
    monkeypatch.setattr(module, 'DepthWorker', Depth)
    monkeypatch.setattr(module, 'choose_device', lambda _: 'cpu')
    monkeypatch.setattr(module, 'THREAT_WEIGHTS', 'missing-test-weapon-model.pt')
    store = Store()
    engine = module.Engine(store)
    # Feed deterministic source-time frames quickly; no real models/cameras used.
    engine.run(SimpleNamespace(mode='live', source='fixture', threshold=.6,
                               hold_seconds=.7, target_fps=1000, eco_mode=False))
    assert engine.state['status'] == 'finished', engine.state.get('message')
    incidents = [payload[0] for kind, payload in store.records if kind == 'incident']
    fights = [event for event in incidents if event['event_type'] == 'fight']
    assert any(state.get('assessment') == 'checking_interaction' for state in progress)
    assert all(not state.get('alert') for state in progress if state.get('source_time', 0) < 5)
    if depth_case == 'matched':
        assert len(fights) == 1
        assert fights[0]['signals']['confirmation_seconds'] >= 5
        assert fights[0]['signals']['source_seconds'] > fights[0]['signals']['depth_source_time']
        assert fights[0]['signals']['depth_samples'] >= 3
    else:
        assert fights == []
        assert not any(state.get('assessment') == 'possible_fight' for state in progress)
    # Pending interactions must not be offered to training as normal scenes.
    assert not [payload for kind, payload in store.records if kind == 'normal_sample']
