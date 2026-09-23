import hashlib
import json
from pathlib import Path
from test_cloud_accounts import cloud, post
from test_cloud_people import setup_team, invite_operator
from desktop.launcher import prepare


def test_download_access_integrity_and_missing_releases(cloud, tmp_path):
    app, mail = cloud
    admin, group = setup_team(app, mail)
    assert app.test_client().get('/downloads').status_code == 403
    assert b'No tested release' in admin.get('/downloads').data
    assert admin.get('/downloads/macos-arm64').status_code == 404
    archive = tmp_path / 'VDM-test.zip'
    archive.write_bytes(b'test artifact only')
    record = dict(platform='macos-arm64', status='published', version='test', filename=archive.name,
                  sha256=hashlib.sha256(archive.read_bytes()).hexdigest(), size=archive.stat().st_size)
    (tmp_path / 'releases.json').write_text(json.dumps([record]))
    app.config['VDM_RELEASE_DIR'] = str(tmp_path)
    assert admin.get('/downloads/macos-arm64').data == b'test artifact only'
    path = invite_operator(admin, mail, group)
    operator = app.test_client()
    post(operator, path, password='operator secure password')
    post(operator, '/login', email='operator@first.test', password='operator secure password')
    assert operator.get('/downloads/macos-arm64').status_code == 200
    archive.write_bytes(b'x' * record['size'])
    assert admin.get('/downloads/macos-arm64').status_code == 503
    record['filename'] = '../outside.zip'
    (tmp_path / 'releases.json').write_text(json.dumps([record]))
    assert admin.get('/downloads/macos-arm64').status_code == 404


def test_launcher_copies_only_shipped_models_preserves_local_data(tmp_path, monkeypatch):
    for key in ['VDM_CLOUD_URL','VDM_CLOUD_ALLOW_LOCAL','VDM_REQUIRE_LICENCE']:
        monkeypatch.setenv(key, '')
    root, destination = tmp_path / 'bundle', tmp_path / 'user'
    (root / 'model-assets').mkdir(parents=True)
    (root / 'model-assets' / 'weights.pt').write_bytes(b'weights')
    (root / 'desktop-config.json').write_text(json.dumps({'cloud_url':'https://cloud.example.test','local_demo':False}))
    data, models = prepare(root, destination)
    assert models.joinpath('weights.pt').read_bytes() == b'weights'
    data.mkdir()
    (data / 'evidence.mp4').write_bytes(b'private')
    (models / 'weights.pt').write_bytes(b'existing')
    prepare(root, destination)
    assert (data / 'evidence.mp4').read_bytes() == b'private'
    assert (models / 'weights.pt').read_bytes() == b'existing'


def test_desktop_instance_lock_reopens_only_local_port_and_releases(tmp_path):
    from desktop.launcher import DesktopInstance
    first, second = DesktopInstance(tmp_path), DesktopInstance(tmp_path)
    assert first.acquire()
    try:
        first.publish_port(12345)
        assert not second.acquire()
        assert second.existing_url() == 'http://127.0.0.1:12345/activation'
        (tmp_path / 'desktop-port.json').write_text('{"port":"https://elsewhere.test"}')
        assert second.existing_url() is None
    finally:
        first.close()
    assert second.acquire()
    assert second.existing_url() is None
    second.close()


def test_desktop_server_uses_available_port():
    from desktop.launcher import create_desktop_server
    first = create_desktop_server()
    second = create_desktop_server()
    try:
        assert first.server_port != second.server_port
        assert first.server_address[0] == '127.0.0.1'
    finally:
        first.server_close()
        second.server_close()


def test_launcher_serves_app_and_opens_assigned_port(tmp_path, monkeypatch):
    from desktop import launcher
    from types import SimpleNamespace
    calls = []
    app = SimpleNamespace(extensions={'vdm_licence': SimpleNamespace(close=lambda: calls.append('licence closed')),
                                     'vmd_manager': SimpleNamespace(close=lambda: calls.append('manager closed'))})
    server = SimpleNamespace(app=None, server_port=45678, serve_forever=lambda: calls.append('served'), server_close=lambda: calls.append('server closed'))
    monkeypatch.setattr(launcher, 'user_directory', lambda: tmp_path)
    monkeypatch.setattr(launcher, 'prepare', lambda *args: (tmp_path / 'data', tmp_path / 'models'))
    monkeypatch.setattr(launcher, 'create_desktop_server', lambda: server)
    monkeypatch.setattr('vmd.app.create_app', lambda *args: app)
    monkeypatch.setattr(launcher.webbrowser, 'open', lambda url: calls.append(url))
    monkeypatch.setattr(launcher.sys, 'argv', ['VDM'])
    monkeypatch.chdir(tmp_path)
    launcher.main()
    assert server.app is app
    assert 'http://127.0.0.1:45678/activation' in calls
    assert 'served' in calls and 'server closed' in calls
    assert not (tmp_path / 'desktop-port.json').exists()
