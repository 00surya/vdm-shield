import pytest
from vmd.licensing import LicenceClient, LicenceError, Denied, CloudTransport, SecureStore
from vmd.models.sources import SourceManager, SourceSettings
from vmd.engine import Engine
from test_cloud_accounts import cloud, post
from test_cloud_people import setup_team
from test_cloud_licensing import provision


class MemoryStore:
    def __init__(self):
        self.value = {}
    def read(self):
        return dict(self.value)
    def write(self, value):
        self.value = dict(value)


def response(token='new'):
    return dict(server_time=1000, valid_until=1300, licence_expires=9000, camera_limit=1, device_id=1, refresh_token=token)


def test_deadline_failure_and_secret_redaction():
    now = [0]
    store = MemoryStore()
    calls = []
    def transport(path, data, token=None):
        calls.append((path, data, token))
        return response()
    client = LicenceClient('https://cloud.test', store=store, transport=transport, clock=lambda: now[0])
    client.activate('x' * 40)
    assert client.allowance() == 1
    assert 'refresh_token' not in client.status()
    def offline(*args):
        raise LicenceError('Offline')
    client.transport = offline
    now[0] = 150
    client.renew()
    assert client.status()['valid']
    now[0] = 301
    assert not client.status()['valid']
    client.transport = transport
    client.renew()
    assert client.status()['valid']
    def revoked(*args):
        raise Denied('Revoked')
    client.transport = revoked
    client.renew()
    assert client.allowance() == 0


def test_secure_storage_failure_does_not_consume_code():
    class Locked(MemoryStore):
        def write(self, value):
            raise LicenceError('Locked')
    calls = []
    client = LicenceClient('https://cloud.test', store=Locked(), transport=lambda *args: calls.append(args))
    with pytest.raises(LicenceError):
        client.activate('x' * 40)
    assert calls == []


def test_cloud_roundtrip_lost_renewal_response_and_revoke(cloud):
    app, mail = cloud
    admin, _ = setup_team(app, mail)
    code = provision(app, admin)
    cloud_client = app.test_client()
    lost = [False]
    def transport(path, data, token=None):
        result = cloud_client.post(path, json=data, headers={'Authorization': 'Bearer ' + token} if token else {})
        if result.status_code in {401, 403}:
            raise Denied('Revoked or expired')
        assert result.status_code == 200, result.data
        if lost[0]:
            lost[0] = False
            raise LicenceError('Response lost')
        return result.json
    store = MemoryStore()
    client = LicenceClient('https://cloud.test', store=store, transport=transport)
    client.activate(code)
    first_token = store.value['refresh_token']
    lost[0] = True
    client.renew()
    assert store.value['refresh_token'] == first_token
    assert 'request_id' in store.value
    client.renew()
    assert client.status()['valid']
    assert store.value['refresh_token'] != first_token
    assert 'request_id' not in store.value
    post(admin, f'/devices/{client.status()["device_id"]}/revoke')
    client.renew()
    assert not client.status()['valid']


def test_source_gate_limits_restart_and_preserves_evidence(tmp_path, monkeypatch):
    monkeypatch.setattr(Engine, 'start', lambda self, settings: setattr(self, 'settings', settings))
    client = LicenceClient('https://cloud.test', store=MemoryStore(), transport=lambda *args: response())
    manager = SourceManager(tmp_path, tmp_path, licence=client)
    try:
        with pytest.raises(ValueError, match='Licence'):
            manager.add(SourceSettings(kind='camera', source='0', name='Gate'))
        client.activate('x' * 40)
        item = manager.add(SourceSettings(kind='camera', source='0', name='Gate'))
        identity = item['camera_id']
        with pytest.raises(ValueError, match='allowance'):
            manager.add(SourceSettings(kind='camera', source='1', name='Other'))
        client.clear()
        assert not manager.get(identity).licence_check()
        with pytest.raises(RuntimeError, match='Licence'):
            manager.set_enabled(identity, True)
        manager.recover_once()
        assert manager.get(identity).stop_event.is_set()
        assert manager.saved_path.exists()
        assert identity in manager.desired
    finally:
        manager.close()


def test_transport_rejects_insecure_and_redirected_origins():
    for url in ['http://example.test', 'https://user:pass@cloud.test', 'https://cloud.test/path', 'https://cloud.test?token=secret']:
        with pytest.raises(ValueError):
            CloudTransport(url)
    CloudTransport('http://127.0.0.1:8766', allow_local=True)
    with pytest.raises(ValueError):
        CloudTransport('http://example.test', allow_local=True)


def test_local_activation_routes_and_processing_gate(tmp_path, monkeypatch):
    from vmd.app import create_app
    monkeypatch.setenv('VDM_CLOUD_URL', 'https://cloud.test')
    monkeypatch.setattr(LicenceClient, 'start', lambda self: None)
    app = create_app(tmp_path, tmp_path)
    try:
        client = app.test_client()
        assert client.get('/activation').status_code == 200
        assert client.get('/api/licence').json['valid'] is False
        assert client.post('/api/licence/clear').status_code == 403
        headers = {'X-VMD-Client': 'dashboard'}
        assert client.post('/api/sources', headers=headers).status_code == 402
        assert client.post('/api/training/train', headers=headers).status_code == 402
        assert client.get('/api/incidents').status_code == 200
        assert client.post('/api/licence/activate', json={'code': 'x' * 40}, headers={**headers, 'Origin': 'https://evil.test'}).status_code == 403
    finally:
        app.extensions['vdm_licence'].close()
        app.extensions['vmd_manager'].close()
