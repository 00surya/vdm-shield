import json
import time
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from test_cloud_accounts import cloud, post, register
from test_cloud_people import setup_team, invite_operator
from test_desktop_licensing import MemoryStore
from test_metadata_sync import snapshot
from vdm_cloud.models import Device, DeviceScope, Entitlement, Group, GroupLicence, Membership, User, TrialGrant, AuditEvent


def operator_account(app, admin, mail, group, email):
    path = invite_operator(admin, mail, group, email)
    client = app.test_client()
    assert post(client, path, password='operator secure password').status_code == 302
    assert post(client, '/login', email=email, password='operator secure password').status_code == 302
    return client


def team(cloud):
    app, mail = cloud
    admin, group = setup_team(app, mail)
    with Session(app.extensions['cloud_engine']) as db:
        owner = db.scalar(select(User).where(User.role == 'admin'))
        db.add(Entitlement(organisation_id=owner.organisation_id, expires=int(time.time()) + 86400,
                           device_limit=5, camera_limit=4))
        policy = db.get(GroupLicence, group)
        policy.device_limit = 3
        policy.camera_limit = 2
        db.commit()
    alice = operator_account(app, admin, mail, group, 'alice@first.test')
    bob = operator_account(app, admin, mail, group, 'bob@first.test')
    assert post(admin, '/groups', name='Block C').status_code == 302
    with Session(app.extensions['cloud_engine']) as db:
        second = db.scalar(select(Group).where(Group.name == 'Block C')).id
    return app, mail, admin, group, second, alice, bob


def code(client, group, name='Block B Mac'):
    response = post(client, '/devices', name=name, group_id=group)
    assert response.status_code == 200, response.data
    return response.data.decode().split('<code class="activation">')[1].split('</code>')[0]


def activate(app, secret):
    response = app.test_client().post('/api/device/activate', json={'activation_code': secret})
    assert response.status_code == 200, response.data
    return response.json


def test_multiple_operators_activate_only_their_groups_and_share_group_health(cloud):
    app, mail, admin, group, second, alice, bob = team(cloud)
    first = activate(app, code(alice, group, 'Alice workstation'))
    other = activate(app, code(bob, group, 'Bob workstation'))
    hidden = activate(app, code(admin, second, 'Private Block C device'))
    assert first['group_id'] == group and first['camera_limit'] == 2
    assert first['owner_email'] == 'alice@first.test'
    assert b'Alice workstation' in bob.get('/devices').data
    assert b'Private Block C device' not in alice.get('/devices').data
    assert alice.get(f'/devices?group={second}').status_code == 404
    assert post(alice, '/devices', name='Forbidden device', group_id=second).status_code == 403
    assert post(alice, '/devices', name='Unscoped device', role='admin').status_code == 403
    assert post(alice, '/groups', name='Unauthorized').status_code == 403
    assert alice.get('/people').status_code == 403
    assert post(bob, f'/devices/{first["device_id"]}/revoke').status_code == 403
    assert post(alice, f'/devices/{hidden["device_id"]}/revoke').status_code == 404
    assert post(admin, f'/devices/{other["device_id"]}/revoke').status_code == 302
    for result in (first, hidden):
        assert app.test_client().post('/api/device/metadata', json=snapshot(),
            headers={'Authorization': 'Bearer ' + result['refresh_token']}).status_code == 200
    assert b'Alice workstation' in bob.get('/insights').data
    assert b'Private Block C device' not in bob.get('/insights').data
    assert alice.get('/downloads').status_code == 200
    assert alice.get('/activity').status_code == 403
    assert b'Passw' not in admin.get('/activity').data


def test_removing_membership_revokes_pending_active_and_rotated_credentials(cloud):
    app, mail, admin, group, second, alice, bob = team(cloud)
    first = activate(app, code(alice, group))
    pending = code(alice, group, 'Pending Alice Mac')
    bob_device = activate(app, code(bob, group, 'Bob shared group'))
    api = app.test_client()
    request_id = 'persistent-renewal-request-1'
    old_headers = {'Authorization': 'Bearer ' + first['refresh_token']}
    renewed = api.post('/api/device/renew', json={'request_id': request_id}, headers=old_headers).json
    with Session(app.extensions['cloud_engine']) as db:
        user_id = db.scalar(select(User).where(User.email == 'alice@first.test')).id
    assert post(admin, f'/people/{user_id}/groups').status_code == 302
    assert api.post('/api/device/activate', json={'activation_code': pending}).status_code == 401
    assert api.post('/api/device/renew', json={'request_id': request_id}, headers=old_headers).status_code == 401
    assert api.post('/api/device/check', json={'lease': renewed['lease']}).status_code == 401
    assert api.post('/api/device/metadata', json=snapshot(),
        headers={'Authorization': 'Bearer ' + renewed['refresh_token']}).status_code == 401
    assert api.post('/api/device/check', json={'lease': bob_device['lease']}).status_code == 200
    assert b'No groups assigned' in alice.get('/groups').data


def test_group_caps_disable_and_cross_organisation_rules(cloud):
    app, mail, admin, group, second, alice, bob = team(cloud)
    assert post(admin, f'/groups/{group}/licence', device_limit=1, camera_limit=1, enabled='yes').status_code == 302
    first = activate(app, code(alice, group))
    assert first['camera_limit'] == 1
    assert post(bob, '/devices', name='Extra device', group_id=group).status_code == 409
    assert post(admin, f'/groups/{group}/licence', device_limit=99, camera_limit=99, enabled='yes').status_code == 400
    assert post(alice, f'/groups/{group}/licence', device_limit=2, camera_limit=4, enabled='yes').status_code == 403
    assert post(admin, f'/groups/{group}/licence', device_limit=1, camera_limit=1).status_code == 302
    api = app.test_client()
    assert api.post('/api/device/renew', json={}, headers={'Authorization': 'Bearer ' + first['refresh_token']}).status_code == 403
    assert api.post('/api/device/check', json={'lease': first['lease']}).status_code == 403
    assert api.post('/api/device/metadata', json=snapshot(), headers={'Authorization': 'Bearer ' + first['refresh_token']}).status_code == 403
    other, other_group = setup_team(app, mail, 'other@different.test')
    assert post(other, f'/groups/{group}/licence', device_limit=1, camera_limit=1, enabled='yes').status_code == 404
    assert post(alice, '/devices', name='Foreign', group_id=other_group).status_code == 404


def test_existing_member_can_join_multiple_groups_and_suspend_revokes_devices(cloud):
    app, mail, admin, group, second, alice, bob = team(cloud)
    assert post(admin, '/invitations', email='alice@first.test', group_id=second).status_code == 302
    assert b'Block C' in alice.get('/groups').data
    device = activate(app, code(alice, second))
    with Session(app.extensions['cloud_engine']) as db:
        user = db.scalar(select(User).where(User.email == 'alice@first.test'))
        identity = user.id
        assert len(db.scalars(select(Membership).where(Membership.user_id == identity)).all()) == 2
        assert db.get(DeviceScope, device['device_id']).group_id == second
    post(admin, f'/people/{identity}/access', action='suspend')
    assert alice.get('/downloads').status_code == 403
    assert app.test_client().post('/api/device/check', json={'lease': device['lease']}).status_code == 401


def test_trial_is_bounded_once_and_pending_invites_do_not_become_admins(cloud):
    app, mail = cloud
    app.config['TRIAL_DAYS'] = 14
    admin, group = setup_team(app, mail)
    with Session(app.extensions['cloud_engine']) as db:
        entitlement = db.scalar(select(Entitlement))
        assert entitlement.device_limit == 2 and entitlement.camera_limit == 4
        assert 13 * 86400 < entitlement.expires - time.time() <= 14 * 86400
        original_expiry = entitlement.expires
        assert len(db.scalars(select(TrialGrant)).all()) == 1
    verification = urlsplit(mail[0][1]).path
    post(admin, verification, password='a different long password')
    with Session(app.extensions['cloud_engine']) as db:
        assert db.scalar(select(Entitlement)).expires == original_expiry
    path = invite_operator(admin, mail, group, 'invited@first.test')
    pending = app.test_client()
    assert post(pending, '/signup', email='invited@first.test', organisation='Wrong organization',
                password='a long secure password').status_code == 302
    with Session(app.extensions['cloud_engine']) as db:
        assert not db.scalar(select(User).where(User.email == 'invited@first.test'))
    assert post(pending, path, password='operator secure password').status_code == 302
    with Session(app.extensions['cloud_engine']) as db:
        assert db.scalar(select(User).where(User.email == 'invited@first.test')).role == 'operator'


def test_password_reset_one_use_and_revokes_sessions(cloud):
    app, mail = cloud
    admin, _ = setup_team(app, mail)
    outsider = app.test_client()
    assert post(outsider, '/forgot-password', email='admin@first.test').status_code == 302
    path = urlsplit(mail[-1][1]).path
    assert post(outsider, path, password='new long secure password').status_code == 302
    assert admin.get('/dashboard').status_code == 302
    assert post(outsider, path, password='different long password').status_code == 400
    assert post(outsider, '/login', email='admin@first.test', password='new long secure password').status_code == 302


def test_development_downloads_are_not_public_releases(cloud, tmp_path):
    import hashlib
    app, mail = cloud
    admin, _ = setup_team(app, mail)
    artifact = tmp_path / 'preview.dmg'
    artifact.write_bytes(b'local-only test artifact')
    (tmp_path / 'releases.json').write_text(json.dumps([dict(platform='macos-arm64', version='test',
        status='development', filename=artifact.name, size=artifact.stat().st_size,
        sha256=hashlib.sha256(artifact.read_bytes()).hexdigest())]))
    app.config['VDM_RELEASE_DIR'] = str(tmp_path)
    assert admin.get('/downloads/macos-arm64').status_code == 404
    app.config['ALLOW_DEVELOPMENT_DOWNLOADS'] = True
    assert admin.get('/downloads/macos-arm64').status_code == 200
    assert b'Development preview' in admin.get('/downloads').data


def test_desktop_switches_isolated_workspaces_and_locks_expired_group(tmp_path, monkeypatch):
    from vmd.app import create_app
    from vmd.licensing import LicenceClient
    from test_desktop_licensing import response
    monkeypatch.setenv('VDM_CLOUD_URL', 'https://cloud.test')
    monkeypatch.setattr(LicenceClient, 'start', lambda self: None)
    app = create_app(tmp_path / 'data', tmp_path / 'models')
    local = app.extensions['vdm_licence']
    local.store = MemoryStore()
    clock = [0]
    local.clock = lambda: clock[0]
    try:
        original = app.extensions['vmd_manager']
        first = {**response(), 'organisation_id': 2, 'group_id': 10, 'group_name': 'Block B'}
        local.apply(first, 0)
        group_b = app.extensions['vmd_manager']
        assert original.closed
        assert group_b.data_dir == tmp_path / 'data/workspaces/org-2/group-10'
        (group_b.data_dir / 'retained-evidence.txt').write_text('private block B evidence')
        local.apply({**first, 'group_id': 20, 'group_name': 'Block C'}, 0)
        group_c = app.extensions['vmd_manager']
        assert group_b.closed and group_c.data_dir.name == 'group-20'
        assert not (group_c.data_dir / 'retained-evidence.txt').exists()
        clock[0] = 301
        assert app.test_client().get('/api/incidents').status_code == 402
        assert app.test_client().get('/activation').status_code == 200
        local.clear()
        assert app.extensions['vmd_manager'].data_dir.name == 'awaiting-activation'
        local.apply(first, clock[0])
        assert (app.extensions['vmd_manager'].data_dir / 'retained-evidence.txt').read_text() == 'private block B evidence'
    finally:
        local.close()
        app.extensions['vmd_manager'].close()
