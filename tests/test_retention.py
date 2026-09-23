import hashlib
import numpy as np

from vmd.app import create_app
from vmd.engine import evidence_frame_720p


def test_saved_annotated_frames_fit_720p_without_upscaling():
    assert evidence_frame_720p(np.zeros((1080, 1920, 3), np.uint8)).shape[:2] == (720, 1280)
    assert evidence_frame_720p(np.zeros((480, 640, 3), np.uint8)).shape[:2] == (480, 640)


def test_new_evidence_retention_setting_and_expiry(tmp_path):
    app = create_app(tmp_path)
    store = app.extensions['vmd_manager'].store
    client = app.test_client()
    try:
        assert client.get('/api/evidence-retention').json['days'] == 7
        assert client.put('/api/evidence-retention', json={'days': 30}).status_code == 403
        assert client.put('/api/evidence-retention', json={'days': 0},
                          headers={'X-VMD-Client': 'dashboard'}).status_code == 400
        response = client.put('/api/evidence-retention', json={'days': 30},
                              headers={'X-VMD-Client': 'dashboard'})
        assert response.json['days'] == 30
        store._incident({'id': 'new', 'created': 1000, 'mode': 'live', 'score': .5,
                         'reasons': [], 'signals': {}}, [])
        with store.connect() as conn:
            expiry = conn.execute("SELECT evidence_expires_at FROM incidents WHERE id='new'").fetchone()[0]
        assert expiry == 1000 + 30 * 86400

        (store.clips / 'expired.avi').write_bytes(b'evidence')
        (store.raw_clips / 'expired-raw.avi').write_bytes(b'raw')
        (store.clips / 'legacy.avi').write_bytes(b'legacy')
        (store.raw_clips / 'sample.avi').write_bytes(b'normal')
        playback = tmp_path / 'playback'
        playback.mkdir()
        cache = playback / (hashlib.sha256(b'expired').hexdigest() + '-test.mp4')
        cache.write_bytes(b'cache')
        with store.connect() as conn:
            conn.execute("INSERT INTO incidents (id,created,mode,score,reasons,signals,clip,raw_clip,evidence_expires_at) VALUES ('expired',1,'live',.5,'[]','{}','expired.avi','expired-raw.avi',2)")
            conn.execute("INSERT INTO incidents (id,created,mode,score,reasons,signals,clip) VALUES ('legacy',1,'live',.5,'[]','{}','legacy.avi')")
            conn.execute("INSERT INTO normal_samples (id,created,camera_id,raw_clip,expires_at) VALUES ('sample',1,'cam','sample.avi',2)")
        assert store.expire_evidence(now=3) == 2
        assert not (store.clips / 'expired.avi').exists()
        assert not (store.raw_clips / 'expired-raw.avi').exists()
        assert not (store.raw_clips / 'sample.avi').exists()
        assert not cache.exists()
        assert (store.clips / 'legacy.avi').exists()
        with store.connect() as conn:
            assert conn.execute("SELECT clip,raw_clip FROM incidents WHERE id='expired'").fetchone() == (None, None)
            assert conn.execute("SELECT COUNT(*) FROM normal_samples WHERE id='sample'").fetchone()[0] == 0
        assert client.get('/api/evidence-retention').json['days'] == 30
    finally:
        app.extensions['vmd_manager'].close()
    restarted = create_app(tmp_path)
    try:
        assert restarted.test_client().get('/api/evidence-retention').json['days'] == 30
    finally:
        restarted.extensions['vmd_manager'].close()
