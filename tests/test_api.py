import io
import re
import time

from vmd.app import create_app
from vmd.engine import Engine
from vmd.capacity import capacity_report, estimate_capacity

HEADERS = {'X-VMD-Client': 'dashboard'}


def test_flask_upload_camera_validation_and_controls(tmp_path, monkeypatch):
    monkeypatch.setattr(Engine, 'start', lambda self, settings: setattr(self, 'settings', settings))
    app = create_app(tmp_path)
    client = app.test_client()
    try:
        assert client.get('/').status_code == 200
        page = client.get('/').data
        assert b'Upload video' in page and b'Depth model:' not in page
        ids = re.findall(rb'\bid="([^"]+)"', page)
        assert len(ids) == len(set(ids))
        assert b'sources' in ids and b'source-list' in ids
        assert b'DPT Hybrid' not in page and b'DPT Large' not in page
        assert client.post('/api/sources', data={'kind': 'camera', 'source': '0'}).status_code == 403
        assert client.post('/api/sources', data={'kind': 'camera', 'source': '0'}, headers={**HEADERS, 'Origin': 'https://elsewhere.example'}).status_code == 403
        assert client.get('/api/sources', headers={'Host': 'untrusted.example'}).status_code == 403
        assert client.post('/api/sources', data={'kind': 'camera', 'source': 'file:///etc/passwd'}, headers=HEADERS).status_code == 400
        assert client.post('/api/sources', data={'kind': 'camera', 'source': '0', 'depth': 'DPT_Hybrid'}, headers=HEADERS).status_code == 400
        assert client.post('/api/sources', data={'kind': 'upload', 'video': (io.BytesIO(b'x'), 'bad.txt')}, headers=HEADERS).status_code == 400
        result = client.post('/api/sources', data={'kind': 'upload', 'name': 'Test', 'video': (io.BytesIO(b'fake video'), 'clip.mp4')}, headers=HEADERS)
        assert result.status_code == 201
        camera_id = result.json['camera_id']
        engine = app.extensions['vmd_manager'].get(camera_id)
        assert engine.settings.mode == 'live'
        assert engine.settings.kind == 'upload'
        assert not hasattr(engine.settings, 'depth')
        assert engine.settings.source.endswith('.mp4')
        assert len(list((tmp_path / 'uploads').glob('*.mp4'))) == 1
        engine.frames = {'pose': b'pose', 'depth': b'depth'}
        engine.state.update(sequence=5, depth_status='ready', depth_sequence=3, depth_source_time=1,
                            source_time=1, depth_submitted_at=time.time(), last_frame_at=time.time())
        frames = client.get(f'/api/sources/{camera_id}/frames').json
        assert frames['pose'] and frames['depth'] and frames['sequence'] == 5
        unchanged = client.get(f'/api/sources/{camera_id}/frames?pose_after=5&depth_after=3').json
        assert unchanged['pose'] is None and unchanged['depth'] is None
        depth_only = client.get(f'/api/sources/{camera_id}/frames?pose_after=5&depth_after=2').json
        assert depth_only['pose'] is None and depth_only['depth']
        engine.state['depth_submitted_at'] = time.time() - 10
        assert client.get(f'/api/sources/{camera_id}/frames').json['depth'] is None
        engine.frames['threat'] = b'threat'
        engine.state.update(threat_status='ready', threat_sequence=3, threat_submitted_at=time.time())
        assert client.get(f'/api/sources/{camera_id}/frames').json['threat']
        engine.state['threat_submitted_at'] = time.time() - 10
        assert client.get(f'/api/sources/{camera_id}/frames').json['threat'] is None
        assert client.post(f'/api/sources/{camera_id}/remove', headers=HEADERS).status_code == 200
        assert client.get(f'/api/sources/{camera_id}/frames').status_code == 404
        stream = client.post('/api/sources', data={'kind': 'camera', 'source': 'rtsp://127.0.0.1:8554/live', 'eco_mode': 'on'}, headers=HEADERS)
        assert stream.status_code == 201
        stream_id = stream.json['camera_id']
        assert app.extensions['vmd_manager'].get(stream_id).settings.kind == 'camera'
        assert app.extensions['vmd_manager'].get(stream_id).settings.eco_mode is True
        changed = client.put(f'/api/sources/{stream_id}/eco', json={'enabled': False}, headers=HEADERS)
        assert changed.json == {'camera_id': stream_id, 'eco_mode': False, 'eco_state': 'off'}
        assert client.put(f'/api/sources/{stream_id}/eco', json={'enabled': 'yes'}, headers=HEADERS).status_code == 400
    finally:
        app.extensions['vmd_manager'].close()


def test_four_source_limit_and_review(tmp_path, monkeypatch):
    monkeypatch.setattr(Engine, 'start', lambda self, settings: setattr(self, 'settings', settings))
    app = create_app(tmp_path)
    client = app.test_client()
    try:
        for n in range(4):
            assert client.post('/api/sources', data={'kind': 'camera', 'source': str(n)}, headers=HEADERS).status_code == 201
        assert client.post('/api/sources', data={'kind': 'camera', 'source': '4'}, headers=HEADERS).status_code == 409
        assert len(client.get('/api/sources').json['sources']) == 4
        assert client.post('/api/incidents/missing/review', json={'decision': 'confirmed'}, headers=HEADERS).status_code == 404
    finally:
        app.extensions['vmd_manager'].close()


def test_analytics_groups_saved_events_and_people(tmp_path):
    app = create_app(tmp_path)
    client = app.test_client()
    store = app.extensions['vmd_manager'].store
    now = time.time()
    try:
        with store.connect() as conn:
            conn.execute(
                "INSERT INTO incidents (id, created, mode, score, reasons, signals, event_type, review) "
                "VALUES (?, ?, 'live', 0.8, '[]', '{}', 'fight', 'unreviewed')",
                ('recent', now - 30),
            )
            conn.execute(
                "INSERT INTO incidents (id, created, mode, score, reasons, signals, event_type, review) "
                "VALUES (?, ?, 'live', 0.7, '[]', '{}', 'accident', 'confirmed')",
                ('older', now - 2 * 86400),
            )
            conn.execute(
                "INSERT INTO telemetry (created, people, score, mode, camera_id) VALUES (?, 3, 0.2, 'live', 'a')",
                (now - 30,),
            )
        day = client.get('/api/analytics?range=24h&tz_offset=-330').json
        assert sum(item['count'] for item in day['events']) == 1
        assert day['reviews'] == {'unreviewed': 1}
        assert day['types'] == [{'event_type': 'fight', 'count': 1}]
        assert day['people'][0]['peak'] == 3
        expected_hour = int((now - 30 + 19800) / 3600) * 3600 - 19800
        assert day['heatmap_events'][-1] == {'bucket': expected_hour, 'count': 1}
        assert day['heatmap_samples'][-1] == {'bucket': expected_hour, 'samples': 1}
        week = client.get('/api/analytics?range=7d').json
        assert sum(item['count'] for item in week['events']) == 2
        assert client.get('/api/analytics?range=all').status_code == 400
        assert client.get('/api/analytics?tz_offset=1500').status_code == 400
    finally:
        app.extensions['vmd_manager'].close()


def test_capacity_tab_reports_actual_rates_and_calculator_limits(tmp_path, monkeypatch):
    monkeypatch.setattr('vmd.capacity.hardware_snapshot', lambda: {'model': 'Fixture', 'cpu_cores': 8,
                      'architecture': 'arm64', 'memory_gb': 16, 'inference_device': 'CPU'})
    app = create_app(tmp_path)
    client = app.test_client()
    try:
        page = client.get('/').data
        assert b'id="capacity"' in page and b'data-page-link="capacity"' in page
        empty = client.get('/api/capacity').json
        assert empty['verdict'] == 'no_live_measurement' and empty['baseline_fps'] is None
        assert empty['hardware']['memory_gb'] == 16

        measured = capacity_report([{'camera_id': 'a', 'name': 'Gate', 'status': 'running',
                                     'capture_fps': 25, 'target_fps': 20, 'processed_fps': 18.4,
                                     'processed_frames': 100, 'skipped_frames': 15,
                                     'depth_status': 'ready', 'threat_status': 'ready'}])
        assert measured['verdict'] == 'keeping_up' and measured['baseline_fps'] == 18.4
        measured = capacity_report([{'status': 'running', 'capture_fps': 25, 'target_fps': 20,
                                     'processed_fps': 12, 'processed_frames': 100}])
        assert measured['verdict'] == 'behind'
        eco = capacity_report([{'camera_id': 'eco', 'source_kind': 'camera', 'status': 'running',
                                'capture_fps': 25, 'target_fps': 20, 'processed_fps': 1,
                                'processed_frames': 100, 'eco_mode': True, 'eco_state': 'quiet',
                                'eco_skipped_frames': 900, 'eco_active_seconds': 10,
                                'eco_quiet_seconds': 20, 'eco_active_pose_frames': 100,
                                'eco_quiet_pose_frames': 20,
                                'depth_status': 'ready', 'threat_status': 'ready'}])
        assert eco['verdict'] == 'keeping_up'
        assert eco['baseline_fps'] is None
        assert eco['device']['eco_cameras'] == 1
        assert eco['device']['eco_quiet_cameras'] == 1
        assert eco['device']['eco_skipped_frames'] == 900
        assert eco['device']['eco_comparison'] == {
            'ready': True, 'camera_count': 1, 'normal_pose_scans_estimate': 300,
            'actual_pose_scans': 120, 'pose_scans_saved_estimate': 180,
            'pose_reduction_pct': 60.0}
        completed = capacity_report([{'camera_id': 'video', 'source_kind': 'upload', 'status': 'finished',
                                      'source_fps': 25, 'capture_fps': 0, 'processed_fps': 0,
                                      'last_processed_fps': 18.4, 'processed_frames': 671}])
        assert completed['verdict'] == 'completed'
        assert completed['baseline_source'] == 'completed_video'
        assert completed['sources'][0]['input_fps'] == 25
        assert completed['sources'][0]['analyzed_fps'] == 18.4

        estimate = client.post('/api/capacity/estimate', headers=HEADERS, json={
            'cameras': 4, 'target_fps': 15, 'source_fps': 25, 'baseline_fps': 18.4,
            'headroom_pct': 25, 'bitrate_mbps': 4, 'retention_days': 7,
            'unit_cost_inr': 900, 'camera_cost_inr': 900,
        })
        assert estimate.status_code == 200
        assert estimate.json['current_machine_equivalents'] is None
        assert estimate.json['estimated_purchase_inr'] is None
        assert estimate.json['camera_cost_inr'] == 3600
        assert estimate.json['required_one_camera_baseline_fps'] == 20
        assert not estimate.json['single_camera_fits']
        assert estimate.json['recording_storage_gb'] > 1000
        feasible = estimate_capacity({'cameras': 4, 'target_fps': 10, 'source_fps': 25,
                                      'baseline_fps': 18.4, 'headroom_pct': 25,
                                      'unit_cost_inr': 100000, 'camera_cost_inr': 900})
        assert feasible['cameras_per_unit'] == 1
        assert feasible['current_machine_equivalents'] == 4
        assert feasible['machine_cost_inr'] == 400000
        assert feasible['estimated_purchase_inr'] == 403600
        assert client.post('/api/capacity/estimate', headers=HEADERS, json={'cameras': 0}).status_code == 400
        assert client.post('/api/capacity/estimate', json={}).status_code == 403
    finally:
        app.extensions['vmd_manager'].close()


def test_device_status_reports_camera_faults_and_actual_disk(tmp_path, monkeypatch):
    from collections import namedtuple
    usage = namedtuple("usage", "total used free")
    gb = 1024**3
    monkeypatch.setattr('vmd.capacity.shutil.disk_usage', lambda _: usage(100 * gb, 96 * gb, 4 * gb))
    monkeypatch.setattr('vmd.capacity.hardware_snapshot', lambda: {})
    result = capacity_report([
        {'name': 'Upload', 'source_kind': 'upload', 'status': 'running'},
        {'name': 'Gate', 'source_kind': 'camera', 'status': 'running', 'stale': True},
        {'name': 'Lobby', 'source_kind': 'camera', 'status': 'error'},
    ], tmp_path)['device']
    assert result['configured_cameras'] == 2
    assert result['online_cameras'] == 0
    assert result['status'] == 'attention'
    assert result['storage'] == {'total_gb': 100, 'used_gb': 96, 'free_gb': 4, 'used_pct': 96, 'low': True}
    assert any('Gate: no live video' in issue for issue in result['issues'])
    assert any('Lobby: no live video' in issue for issue in result['issues'])
    assert any('nearly full' in issue for issue in result['issues'])


def test_device_status_ready_requires_fresh_workers(tmp_path, monkeypatch):
    monkeypatch.setattr('vmd.capacity.hardware_snapshot', lambda: {})
    camera = {'source_kind': 'camera', 'status': 'running', 'capture_fps': 20,
              'target_fps': 20, 'processed_fps': 20, 'processed_frames': 100,
              'depth_status': 'ready', 'threat_status': 'ready'}
    from collections import namedtuple
    usage = namedtuple("usage", "total used free")
    monkeypatch.setattr('vmd.capacity.shutil.disk_usage', lambda _: usage(100 * 1024**3, 0, 100 * 1024**3))
    healthy = capacity_report([camera], tmp_path)['device']
    assert healthy['status'] == 'ready'
    assert healthy['healthy_cameras'] == 1
    assert healthy['schema_version'] == 2
    assert healthy['service_uptime_seconds'] >= 0
    assert healthy['checked_at'] > 0
    camera['threat_stale'] = True
    delayed = capacity_report([camera], tmp_path)['device']
    assert delayed['status'] == 'attention'
    assert delayed['healthy_cameras'] == 0
    assert capacity_report([], tmp_path)['device']['status'] == 'idle'
