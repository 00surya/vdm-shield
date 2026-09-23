import time
import cv2
import numpy as np
from vmd.app import create_app


def test_history_boundary_search_and_pagination(tmp_path, monkeypatch):
    import vmd.controllers.events as module
    monkeypatch.setattr(module.time, 'time', lambda: 100000.)
    app = create_app(tmp_path)
    store = app.extensions['vmd_manager'].store
    client = app.test_client()
    try:
        with store.connect() as conn:
            for index in range(35):
                conn.execute("INSERT INTO incidents (id,created,mode,score,reasons,signals,camera_name,event_type) VALUES (?,?,'live',.8,'[]','{}','Entrance','fight')", (str(index), 56800 - index))
        recent = client.get('/api/event-records?scope=recent').json
        assert recent['total'] == 1 and recent['items'][0]['id'] == '0'
        history = client.get('/api/event-records?scope=history').json
        assert history['total'] == 34 and history['pages'] == 2
        assert len(client.get('/api/event-records?scope=history&page=2').json['items']) == 4
        assert client.get('/api/event-records?scope=history&from=56799&to=56799').json['total'] == 1
        assert client.get('/api/event-records?scope=history&search=entrance').json['total'] == 34
        assert client.get('/api/event-records?scope=history&review=confirmed').json['total'] == 0
        assert client.get('/api/event-records?from=nan').status_code == 400
        assert client.get('/api/event-records?from=2&to=1').status_code == 400
        page = client.get('/').text
        assert 'data-page="history"' in page and 'data-page="evidence"' not in page
    finally:
        app.extensions['vmd_manager'].close()


def test_evidence_mp4_playback_range_and_deletion(tmp_path):
    app = create_app(tmp_path)
    store = app.extensions['vmd_manager'].store
    client = app.test_client()
    try:
        writer = cv2.VideoWriter(str(store.clips / 'test.avi'), cv2.VideoWriter_fourcc(*'MJPG'), 5, (64, 48))
        for index in range(10): writer.write(np.full((48, 64, 3), index * 20, np.uint8))
        writer.release()
        with store.connect() as conn:
            conn.execute("INSERT INTO incidents (id,created,mode,score,reasons,signals,clip) VALUES ('test',?,'live',.8,'[]','{}','test.avi')", (time.time(),))
        response = client.get('/api/library/evidence/test/play')
        assert response.status_code == 200 and response.mimetype == 'video/mp4'
        assert b'ftyp' in response.data[:32]
        assert client.get('/api/library/evidence/test/play', headers={'Range': 'bytes=0-99'}).status_code == 206
        assert list((tmp_path / 'playback').glob('*.mp4'))
        assert client.delete('/api/library/evidence/test', headers={'X-VMD-Client': 'dashboard'}).status_code == 200
        assert not list((tmp_path / 'playback').glob('*.mp4'))
        assert client.get('/api/library/evidence/test/play').status_code == 404
    finally:
        app.extensions['vmd_manager'].close()
