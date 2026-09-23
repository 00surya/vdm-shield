import io
import cv2
import numpy as np

from vmd.app import create_app
from vmd.engine import Engine

HEADERS = {'X-VMD-Client': 'dashboard'}


def test_video_library_persists_and_protects_sources(tmp_path, monkeypatch):
    monkeypatch.setattr(Engine, 'start', lambda self, settings: setattr(self, 'settings', settings))
    app = create_app(tmp_path)
    manager = app.extensions['vmd_manager']
    client = app.test_client()
    try:
        assert client.post('/api/library/videos', data={}, headers=HEADERS).status_code == 400
        result = client.post('/api/library/videos', data={'video': (io.BytesIO(b'fixture'), 'My video.mp4')}, headers=HEADERS)
        assert result.status_code == 201
        filename = result.json['id']
        url = '/api/library/videos/' + filename
        assert client.get('/api/library').json['videos'][0]['name'] == 'My video.mp4'
        assert client.get(url).data == b'fixture'
        assert client.delete(url).status_code == 403
        source = client.post(url + '/analyze', headers=HEADERS)
        assert source.status_code == 201
        assert client.delete(url, headers=HEADERS).status_code == 409
        manager.remove(source.json['camera_id'])
        assert client.delete(url, headers=HEADERS).status_code == 200
        assert client.get('/api/library').json['videos'] == []
        assert client.get(url).status_code == 404
        (manager.uploads / 'secret.mp4').symlink_to('/etc/passwd')
        assert client.get('/api/library/videos/secret.mp4').status_code == 404
    finally:
        manager.close()


def test_evidence_preview_and_delete_keeps_event(tmp_path):
    app = create_app(tmp_path)
    manager = app.extensions['vmd_manager']
    client = app.test_client()
    store = manager.store
    try:
        for directory in (store.clips, store.raw_clips):
            writer = cv2.VideoWriter(str(directory / 'event.avi'), cv2.VideoWriter_fourcc(*'MJPG'), 5, (64, 48))
            for index in range(5):
                writer.write(np.full((48, 64, 3), index * 40, np.uint8))
            writer.release()
        with store.connect() as conn:
            conn.execute("INSERT INTO incidents (id,created,mode,score,reasons,signals,clip,raw_clip) VALUES ('event',1,'live',.8,'[]','{}','event.avi','event.avi')")
        assert client.get('/api/library').json['evidence'][0]['raw_available']
        url = '/api/library/evidence/event'
        response = client.get(url + '/preview?position=100')
        assert response.status_code == 200 and response.mimetype == 'image/jpeg'
        assert client.get(url + '/preview?position=-1').status_code == 400
        assert client.get(url + '?kind=raw').status_code == 200
        assert client.delete(url).status_code == 403
        assert client.delete(url, headers=HEADERS).status_code == 200
        assert client.get(url).status_code == 404
        assert not (store.raw_clips / 'event.avi').exists()
        item = client.get('/api/library').json['evidence'][0]
        assert not item['annotated_available'] and not item['raw_available']
        assert store.incidents()[0]['id'] == 'event'
    finally:
        manager.close()


def test_recorded_video_frames_metadata_navigation_and_download(tmp_path):
    app = create_app(tmp_path)
    manager = app.extensions['vmd_manager']
    client = app.test_client()
    try:
        path = manager.uploads / 'recorded.avi'
        writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*'MJPG'), 5, (64, 48))
        for index in range(5):
            writer.write(np.full((48, 64, 3), index * 40, np.uint8))
        writer.release()
        url = '/api/library/videos/recorded.avi/preview'
        meta = client.get(url + '?metadata=1').json
        assert meta['frames'] == 5 and meta['duration_seconds'] == 1
        first = client.get(url + '?frame=0')
        last = client.get(url + '?frame=4&download=1')
        assert first.status_code == last.status_code == 200
        assert first.data != last.data
        assert 'attachment' in last.headers['Content-Disposition']
        assert 'frame-5.jpg' in last.headers['Content-Disposition']
        for value in ('-1', '5', 'abc', '1.5'):
            assert client.get(url + '?frame=' + value).status_code == 400
        page = client.get('/').text
        for prefix in ('video', 'history'):
            assert f'id="{prefix}-from"' in page and f'id="{prefix}-to"' in page
    finally:
        manager.close()
