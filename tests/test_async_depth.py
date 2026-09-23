import queue
import time
from types import SimpleNamespace

import numpy as np

from vmd.app import create_app
from vmd.depth_worker import DepthWorker, replace_latest
from vmd.engine import Engine
from vmd.storage import Store


def echo_depth(requests, responses, stop, *args):
    sequence, source_time, submitted_at, frame = requests.get(timeout=5)
    responses.put({"status": "ready", "sequence": sequence, "source_time": source_time,
                   "submitted_at": submitted_at, "latency_ms": 10, "jpeg": b"fixture"})
    stop.wait(10)


def stuck_depth(requests, responses, stop, *args):
    time.sleep(15)


def test_worker_rate_limit_provenance_and_shutdown():
    worker=DepthWorker('cpu','unused',fps=.1,runner=echo_depth)
    worker.start()
    try:
        frame=np.zeros((4,4,3),np.uint8)
        assert worker.submit(23,4.2,frame)
        assert not worker.submit(24,4.3,frame)
        deadline=time.monotonic()+5
        result=None
        while time.monotonic()<deadline:
            _,result,error=worker.poll()
            if result:break
            time.sleep(.02)
        assert result and result['sequence']==23 and result['source_time']==4.2
        assert not error
    finally:
        worker.stop()
    assert not worker.process.is_alive()


def test_hung_depth_can_be_stopped_without_hanging_session():
    worker=DepthWorker('cpu','unused',runner=stuck_depth)
    worker.start()
    started=time.monotonic()
    worker.stop()
    assert time.monotonic()-started<3
    assert not worker.process.is_alive()


def test_latest_queue_stays_bounded():
    channel=queue.Queue(maxsize=1)
    for n in range(100):replace_latest(channel,n)
    assert channel.qsize()==1 and channel.get_nowait()==99


def test_event_frame_bypasses_rate_limit_and_cannot_be_replaced_by_routine_sample():
    worker = DepthWorker.__new__(DepthWorker)
    worker.requests = queue.Queue(maxsize=1)
    worker.responses = queue.Queue(maxsize=1)
    worker.process = SimpleNamespace(exitcode=None)
    worker.closed = False
    worker.status = 'ready'
    worker.error = worker.result = None
    worker.interval = 10
    worker.last_submit = float('-inf')
    worker.priority_sequence = worker.priority_at = None
    frame = np.zeros((4, 4, 3), np.uint8)
    assert worker.submit(1, 0, frame)
    assert worker.submit(2, .1, frame, priority=True)
    assert not worker.submit(3, .2, frame)
    assert worker.requests.get_nowait()[0] == 2
    worker.responses.put({'status': 'ready', 'sequence': 2, 'source_time': .1,
                          'submitted_at': time.time(), 'latency_ms': 1, 'jpeg': b'frame'})
    assert worker.poll()[1]['sequence'] == 2
    assert worker.priority_sequence is None
    assert worker.submit(4, .3, frame, priority=True)


def test_depth_provenance_and_stale_images_are_filtered(tmp_path):
    app=create_app(tmp_path)
    client=app.test_client()
    engine=Engine(app.extensions['vmd_manager'].store)
    app.extensions['vmd_manager'].cameras[engine.camera_id]=engine
    try:
        engine.frames={'pose':b'pose','depth':b'depth'}
        engine.state.update(sequence=20,source_time=2,depth_status='ready',depth_sequence=10,
                            depth_source_time=1,depth_submitted_at=time.time(),last_frame_at=time.time())
        bundle=client.get(f'/api/sources/{engine.camera_id}/frames').json
        assert bundle['sequence']==20 and bundle['depth_meta']['sequence']==10
        assert bundle['depth_meta']['source_time']==1
        assert bundle['depth'] and not bundle['depth_meta']['stale']
        engine.state['depth_submitted_at']=time.time()-10
        bundle=client.get(f'/api/sources/{engine.camera_id}/frames').json
        assert bundle['depth'] is None and bundle['pose']
    finally:
        app.extensions['vmd_manager'].close()


def test_pose_keeps_advancing_during_depth_loading_and_failure(monkeypatch,tmp_path):
    import vmd.engine as module
    class FakeCapture:
        def __init__(self,source):
            self.finished=False
            self.error=None
            self.stop_event=__import__('threading').Event()
        def start(self):pass
        def stop(self):pass
        def latest(self,after):return after+1,(after+1)*.1,np.zeros((64,96,3),np.uint8)
    class FakePose:
        def __init__(self,*args):pass
        def infer(self,frame):return []
    class FakeDepth:
        instance=None
        def __init__(self,*args,**kwargs):
            FakeDepth.instance=self
            self.failed=False
            self.stopped=False
        def start(self):pass
        def submit(self,*args):pass
        def poll(self):return ('error',None,'Fixture depth failure') if self.failed else ('loading',None,None)
        def stop(self):self.stopped=True
    monkeypatch.setattr(module,'Capture',FakeCapture)
    monkeypatch.setattr(module,'PoseModel',FakePose)
    monkeypatch.setattr(module, 'THREAT_WEIGHTS', 'test-no-threat-weights.pt')
    monkeypatch.setattr(module,'choose_device',lambda device:'cpu')
    monkeypatch.setattr(module,'DepthWorker',FakeDepth)
    store=Store(tmp_path)
    engine=Engine(store)
    settings=SimpleNamespace(mode='live',source='fixture',threshold=.68,hold_seconds=1.2,target_fps=30)
    try:
        engine.start(settings)
        deadline=time.monotonic()+3
        while engine.snapshot()['sequence']<6 and time.monotonic()<deadline:time.sleep(.01)
        state=engine.snapshot()
        assert state['status']=='running' and state['sequence']>=6
        assert state['depth_meta']['status']=='loading'
        FakeDepth.instance.failed=True
        after=state['sequence']
        while engine.snapshot()['sequence']<after+4 and time.monotonic()<deadline:time.sleep(.01)
        state=engine.snapshot()
        assert state['sequence']>=after+4 and state['status']=='running'
        assert state['depth_meta']['error']=='Fixture depth failure'
    finally:
        engine.stop()
        store.close()
    assert FakeDepth.instance.stopped


def test_pose_event_sends_its_own_frame_to_both_background_workers(monkeypatch, tmp_path):
    import threading
    import vmd.engine as module
    from vmd.heuristics import Assessment

    class FakeCapture:
        def __init__(self, source):
            self.finished = False
            self.error = None
            self.file = False
            self.stop_event = threading.Event()
        def start(self): pass
        def stop(self): pass
        def latest(self, after):
            sequence = after + 1
            return sequence, sequence * .1, np.full((64, 96, 3), sequence, np.uint8)

    class FakePose:
        seen = []
        def __init__(self, *args): pass
        def infer(self, frame):
            self.seen.append(int(frame[0, 0, 0]))
            return []

    class FakeBehavior:
        def __init__(self, *args):
            self.fight = SimpleNamespace(rules=SimpleNamespace(camera_speed=.09, max_gap_seconds=.65))
            self.fired = False
        def update(self, people, timestamp, flow, camera):
            if timestamp >= .3 and not self.fired:
                self.fired = True
                event = Assessment(.8, 'possible_fight', ['fixture event'], {}, (1, 2), True, 'fight')
                return Assessment(.8, 'possible_fight', event.reasons, {}, (1, 2), True, 'fight', [event])
            return Assessment()

    class FakeWorker:
        instances = []
        def __init__(self, *args, **kwargs):
            self.status = 'ready'
            self.calls = []
            self.instances.append(self)
        def start(self): pass
        def stop(self): pass
        def poll(self): return 'ready', None, None
        def submit(self, sequence, source_time, frame, priority=False):
            self.calls.append((sequence, priority, int(frame[0, 0, 0])))
            return True

    models = tmp_path / 'models'
    models.mkdir()
    (models / 'mock-threat.pt').write_bytes(b'fixture')
    monkeypatch.setattr(module, 'Capture', FakeCapture)
    monkeypatch.setattr(module, 'PoseModel', FakePose)
    monkeypatch.setattr(module, 'BehaviorHeuristic', FakeBehavior)
    monkeypatch.setattr(module, 'DepthWorker', FakeWorker)
    monkeypatch.setattr(module, 'ThreatWorker', FakeWorker)
    monkeypatch.setattr(module, 'THREAT_WEIGHTS', 'mock-threat.pt')
    monkeypatch.setattr(module, 'choose_device', lambda _: 'cpu')
    store = Store(tmp_path / 'data')
    engine = Engine(store, models)
    try:
        engine.start(SimpleNamespace(mode='live', source='fixture', threshold=.6,
                                     hold_seconds=.7, target_fps=30))
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if len(FakeWorker.instances) == 2 and all(any(call[1] for call in worker.calls)
                                                       for worker in FakeWorker.instances):
                break
            time.sleep(.01)
        assert len(FakeWorker.instances) == 2
        priority_calls = [[call for call in worker.calls if call[1]] for worker in FakeWorker.instances]
        assert all(len(calls) == 1 for calls in priority_calls)
        assert priority_calls[0][0] == priority_calls[1][0]
        sequence, _, pixel = priority_calls[0][0]
        assert sequence == pixel and pixel in FakePose.seen
        assert engine.snapshot()['status'] == 'running'
    finally:
        engine.stop()
        store.close()
