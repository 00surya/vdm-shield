import cv2
import numpy as np
from vmd.storage import EvidenceBuffer, Store


def test_buffer_bounded_by_time_and_bytes():
    buffer=EvidenceBuffer(seconds=10,max_bytes=25)
    for t in range(20):buffer.append(t,b'12345')
    assert buffer.bytes<=25
    assert len(buffer.snapshot())==5
    buffer.append(50,b'12')
    assert buffer.snapshot()==[(50,b'12')]


def test_clip_roundtrip_review_and_demo_exclusion(tmp_path):
    store=Store(tmp_path)
    image=np.full((100,160,3),80,np.uint8)
    jpeg=cv2.imencode('.jpg',image)[1].tobytes()
    event={'id':'testclip','created':1000,'mode':'demo','score':.8,'reasons':['Fast limbs'],'signals':{}}
    store.enqueue('incident',(event,[(0,jpeg),(.2,jpeg),(.8,jpeg),(1,jpeg)]))
    store.enqueue('telemetry',(1000,2,.8,'demo'))
    store.enqueue('telemetry',(1001,4,.2,'live'))
    store.jobs.join()
    incident=store.incidents()[0]
    assert incident['clip']=='testclip.avi'
    cap=cv2.VideoCapture(str(store.clips/incident['clip']))
    assert cap.isOpened() and cap.read()[0]
    assert 1<=cap.get(cv2.CAP_PROP_FRAME_COUNT)/cap.get(cv2.CAP_PROP_FPS)<=1.2
    cap.release()
    assert store.review('testclip','false_positive')
    assert store.incidents()[0]['review']=='false_positive'
    assert store.analytics(0)[0]['average']==4
    store.close()


def test_incident_update_groups_signals_and_keeps_review(tmp_path):
    store = Store(tmp_path)
    image = np.full((60, 80, 3), 80, np.uint8)
    jpeg = cv2.imencode('.jpg', image)[1].tobytes()
    frames = [(0, jpeg), (.5, jpeg), (1, jpeg)]
    first = {'id': 'one', 'created': 1000, 'mode': 'live', 'score': .8,
             'reasons': ['First'], 'signals': {'source_seconds': 23}}
    next_signal = {**first, 'score': .7, 'reasons': ['Later'],
                   'signals': {'source_seconds': 28}, 'event_count': 2}
    try:
        assert store.enqueue('incident', (first, frames))
        store.jobs.join()
        assert store.review('one', 'confirmed')
        assert store.enqueue('incident_update', (next_signal, frames))
        store.jobs.join()
        incidents = store.incidents()
        assert len(incidents) == 1
        assert incidents[0]['event_count'] == 2
        assert (incidents[0]['source_start'], incidents[0]['source_end']) == (23, 28)
        assert incidents[0]['score'] == .8
        assert incidents[0]['reasons'] == ['First', 'Later']
        assert incidents[0]['review'] == 'confirmed'
        assert incidents[0]['clip'] == 'one.avi'
        assert not store.error
    finally:
        store.close()


def test_clip_times_and_mixed_size_frames_are_preserved(tmp_path):
    store = Store(tmp_path)
    try:
        small = cv2.imencode('.jpg', np.zeros((48, 64, 3), np.uint8))[1].tobytes()
        large = cv2.imencode('.jpg', np.full((96, 128, 3), 220, np.uint8))[1].tobytes()
        frames = [(10., small), (10.5, large), (11., large)]
        event = {'id': 'sizes', 'created': 1, 'mode': 'live', 'score': .8, 'reasons': [], 'signals': {'source_seconds': 10.5}}
        store._incident(event, frames, frames)
        item = store.incidents()[0]
        assert item['source_start'] == 10.5
        assert (item['evidence_start'], item['evidence_end']) == (10., 11.)
        assert (item['raw_start'], item['raw_end']) == (10., 11.)
        cap = cv2.VideoCapture(str(store.clips / item['clip']))
        try:
            assert cap.get(cv2.CAP_PROP_FRAME_COUNT) == 11
            cap.set(cv2.CAP_PROP_POS_FRAMES, 10)
            ok, final = cap.read()
            assert ok and final.mean() > 200
        finally:
            cap.release()
    finally:
        store.close()
