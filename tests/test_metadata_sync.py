import json
import time
from types import SimpleNamespace
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_cloud_accounts import cloud, post
from test_cloud_people import setup_team, invite_operator
from test_cloud_licensing import provision
from test_desktop_licensing import MemoryStore, response
from vmd.metadata import validate_snapshot, collect_snapshot, FIELDS
from vmd.licensing import LicenceClient, LicenceError
from vdm_cloud.models import DeviceSnapshot, Entitlement


def snapshot():
    return dict(schema=1, source_count=2, running_sources=1, stale_sources=0,
                analysis_fps=18.5, free_disk_gb=100, alerts_24h=3,
                confirmed_24h=1, false_positive_24h=1, unreviewed_24h=1)


def test_schema_refuses_media_identifiers_and_invalid_numbers():
    assert validate_snapshot(snapshot()) == snapshot()
    for bad in [{**snapshot(), 'frames': 'base64'}, {**snapshot(), 'organisation_id': 5},
                {**snapshot(), 'analysis_fps': float('nan')}, {**snapshot(), 'analysis_fps': float('inf')},
                {**snapshot(), 'running_sources': True}, {**snapshot(), 'alerts_24h': 9},
                {**snapshot(), 'free_disk_gb': 'rtsp://secret'}, {**snapshot(), 'schema': 2}, {**snapshot(), 'alerts_24h': 10**500}]:
        with pytest.raises(ValueError):
            validate_snapshot(bad)


def test_cloud_authenticated_ingestion_and_dashboard_isolation(cloud):
    app, mail = cloud
    admin, group = setup_team(app, mail)
    other, _ = setup_team(app, mail, 'admin@second.test')
    code = provision(app, admin)
    client = app.test_client()
    activated = client.post('/api/device/activate', json={'activation_code': code}).json
    headers = {'Authorization': 'Bearer ' + activated['refresh_token']}
    assert client.post('/api/device/metadata', json=snapshot()).status_code == 401
    assert client.post('/api/device/metadata', json=snapshot(), headers={**headers, 'Origin': 'https://evil.test'}).status_code == 400
    assert client.post('/api/device/metadata', json={**snapshot(), 'video': 'secret'}, headers=headers).status_code == 400
    assert client.post('/api/device/metadata', json=snapshot(), headers=headers).status_code == 200
    assert client.post('/api/device/metadata', json=snapshot(), headers=headers).status_code == 429
    assert b'18.5 FPS' in admin.get('/insights').data
    assert b'18.5 FPS' not in other.get('/insights').data
    path = invite_operator(admin, mail, group)
    operator = app.test_client()
    post(operator, path, password='operator secure password')
    post(operator, '/login', email='operator@first.test', password='operator secure password')
    assert operator.get('/insights').status_code == 200
    assert b'18.5 FPS' not in operator.get('/insights').data  # unscoped admin device
    with Session(app.extensions['cloud_engine']) as db:
        saved = db.scalar(select(DeviceSnapshot))
        assert set(json.loads(saved.payload)) == FIELDS
        saved.received_at -= 200
        db.commit()
    assert b'Stale snapshot' in admin.get('/insights').data
    assert client.post('/api/device/metadata', json=snapshot(), headers=headers).status_code == 200
    with Session(app.extensions['cloud_engine']) as db:
        assert len(db.scalars(select(DeviceSnapshot)).all()) == 1
    post(admin, f'/devices/{activated["device_id"]}/revoke')
    assert client.post('/api/device/metadata', json=snapshot(), headers=headers).status_code == 401


def test_sync_does_not_change_licence_and_sends_only_allowlist():
    store = MemoryStore()
    calls = []
    def transport(path, data, token=None):
        calls.append((path, data, token))
        return response() if path.endswith('activate') else {'saved': True}
    client = LicenceClient('https://cloud.test', store=store, transport=transport)
    client.activate('x' * 40)
    client.metadata_provider = snapshot
    deadline = client.deadline
    client.sync_metadata()
    assert calls[-1][0] == '/api/device/metadata'
    assert set(calls[-1][1]) == FIELDS
    assert client.status()['last_sync'] is not None
    assert client.deadline == deadline
    client.transport = lambda *args: (_ for _ in ()).throw(LicenceError('offline'))
    client.sync_metadata()
    assert client.status()['valid']
    assert 'unavailable' in client.status()['sync_message']
    assert client.deadline == deadline


def test_collector_discards_sensitive_engine_fields(tmp_path):
    from vmd.storage import Store
    store = Store(tmp_path)
    manager = SimpleNamespace(data_dir=tmp_path, store=store, list=lambda: [dict(status='running', processed_fps=19, name='secret camera', source='rtsp://user:pass@private', frames=b'private', reasons=['private event'])])
    try:
        collected = collect_snapshot(manager)
        assert collected['analysis_fps'] == 19
        assert set(collected) == FIELDS
        assert 'private' not in json.dumps(collected)
    finally:
        store.close()


def test_expiry_rotation_and_interrupted_renewal_block_sync(cloud):
    app, mail = cloud
    admin, _ = setup_team(app, mail)
    code = provision(app, admin)
    client = app.test_client()
    activated = client.post('/api/device/activate', json={'activation_code': code}).json
    old = {'Authorization': 'Bearer ' + activated['refresh_token']}
    renewed = client.post('/api/device/renew', json={}, headers=old).json
    assert client.post('/api/device/metadata', json=snapshot(), headers=old).status_code == 401
    with Session(app.extensions['cloud_engine']) as db:
        entitlement = db.scalar(select(Entitlement))
        entitlement.expires = int(time.time()) - 1
        db.commit()
    assert client.post('/api/device/metadata', json=snapshot(), headers={'Authorization': 'Bearer ' + renewed['refresh_token']}).status_code == 403
    calls = []
    store = MemoryStore()
    local = LicenceClient('https://cloud.test', store=store, transport=lambda *args: calls.append(args))
    local.apply(response(), local.clock())
    store.write({'refresh_token': 'old', 'request_id': 'pending'})
    local.metadata_provider = snapshot
    local.sync_metadata()
    assert calls == []
