import time
from html.parser import HTMLParser

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_cloud_accounts import cloud, post
from test_cloud_people import setup_team
from vdm_cloud.models import Entitlement, Device, DeviceScope, Group, User, AuditEvent


def provision(app, admin, devices=1):
    with Session(app.extensions['cloud_engine']) as db:
        org_id = db.scalar(select(User).where(User.email == 'admin@first.test')).organisation_id
    result = app.test_cli_runner().invoke(args=['grant-licence', '--organisation-id', str(org_id), '--days', '7', '--devices', str(devices)])
    assert result.exit_code == 0
    page = post(admin, '/devices', name='Reception Mac')
    assert page.status_code == 200
    return page.data.decode().split('<code class="activation">')[1].split('</code>')[0]


def test_activation_rotation_revocation_and_limits(cloud):
    app, mail = cloud
    admin, _ = setup_team(app, mail)
    assert post(admin, '/devices', name='Reception Mac').status_code == 403
    code = provision(app, admin)
    assert post(admin, '/devices', name='Extra Mac').status_code == 409
    client = app.test_client()
    activated = client.post('/api/device/activate', json={'activation_code': code})
    assert activated.status_code == 200
    token = activated.json['refresh_token']
    lease = activated.json['lease']
    assert client.post('/api/device/activate', json={'activation_code': code}).status_code == 401
    assert client.post('/api/device/check', json={'lease': lease}).json['valid']
    renewed = client.post('/api/device/renew', json={}, headers={'Authorization': 'Bearer ' + token})
    assert renewed.status_code == 200
    assert renewed.json['licence_expires'] == activated.json['licence_expires']
    assert client.post('/api/device/renew', json={}, headers={'Authorization': 'Bearer ' + token}).status_code == 401
    assert client.post('/api/device/check', json={'lease': lease}).status_code == 401
    with Session(app.extensions['cloud_engine']) as db:
        device = db.get(Device, activated.json['device_id'])
        assert device.activation_hash is None
        assert device.credential_hash != renewed.json['refresh_token']
    assert post(admin, f'/devices/{activated.json["device_id"]}/revoke').status_code == 302
    assert client.post('/api/device/renew', json={}, headers={'Authorization': 'Bearer ' + renewed.json['refresh_token']}).status_code == 401
    assert client.post('/api/device/check', json={'lease': renewed.json['lease']}).status_code == 401
    assert post(admin, '/devices', name='Replacement Mac').status_code == 200


def test_expiry_and_tenant_isolation(cloud):
    app, mail = cloud
    admin, _ = setup_team(app, mail)
    other, _ = setup_team(app, mail, 'admin@second.test')
    code = provision(app, admin)
    with Session(app.extensions['cloud_engine']) as db:
        device = db.scalar(select(Device))
        identity = device.id
        device.activation_expires = int(time.time()) - 1
        db.commit()
    assert other.get('/devices').status_code == 200
    assert b'<h3>Reception Mac</h3>' not in other.get('/devices').data
    assert post(other, f'/devices/{identity}/revoke').status_code == 404
    client = app.test_client()
    assert client.post('/api/device/activate', json={'activation_code': code}).status_code == 401
    code = post(admin, '/devices', name='Replacement').data.decode().split('<code class="activation">')[1].split('</code>')[0]
    activated = client.post('/api/device/activate', json={'activation_code': code}).json
    with Session(app.extensions['cloud_engine']) as db:
        entitlement = db.scalar(select(Entitlement))
        entitlement.expires = int(time.time()) - 1
        db.commit()
    assert client.post('/api/device/renew', json={}, headers={'Authorization': 'Bearer ' + activated['refresh_token']}).status_code == 403
    assert client.post('/api/device/check', json={'lease': activated['lease']}).status_code == 401


def test_api_cookie_csrf_and_input_boundaries(cloud):
    app, mail = cloud
    admin, _ = setup_team(app, mail)
    code = provision(app, admin)
    assert admin.post('/devices', data={'name': 'Unsafe'}).status_code == 400
    assert admin.post('/api/device/renew', json={}).status_code == 401
    assert admin.post('/api/device/activate', json={'activation_code': code}, headers={'Origin': 'https://evil.test'}).status_code == 400
    assert admin.post('/api/device/activate', json={'activation_code': code, 'camera_limit': 999}).status_code == 400
    assert admin.post('/api/device/check', json={'lease': 'tampered'}).status_code == 401
    for data in [[], None, {'activation_code': 123}]:
        assert admin.post('/api/device/activate', json=data).status_code == 400


class ActivationForm(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.name = {}
        self.selected_groups = []
        self.feed(html)

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        if tag == 'input' and attrs.get('name') == 'name':
            self.name = attrs
        if tag == 'option' and 'selected' in attrs:
            self.selected_groups.append(attrs['value'])


def activation_team(cloud):
    app, mail = cloud
    admin, group = setup_team(app, mail)
    with Session(app.extensions['cloud_engine']) as db:
        owner = db.scalar(select(User).where(User.email == 'admin@first.test'))
        db.add(Entitlement(organisation_id=owner.organisation_id, expires=int(time.time()) + 86400,
                           device_limit=3, camera_limit=4))
        db.commit()
    return app, admin, group


@pytest.mark.parametrize('submitted_name', [None, '', '   ', 'A', '  Reception Mac  ', 'M' * 100])
def test_activation_name_is_optional_and_custom_labels_are_preserved(cloud, submitted_name):
    app, admin, group = activation_team(cloud)
    initial = ActivationForm(admin.get(f'/devices?group={group}').text)
    assert 'required' not in initial.name and 'minlength' not in initial.name
    data = {'group_id': group}
    if submitted_name is not None:
        data['name'] = submitted_name
    response = post(admin, '/devices', **data)
    assert response.status_code == 200
    form = ActivationForm(response.text)
    assert form.selected_groups == [str(group)]
    assert form.name['value'] == ''
    with Session(app.extensions['cloud_engine']) as db:
        device = db.scalar(select(Device))
        if submitted_name and submitted_name.strip():
            assert device.name == submitted_name.strip()
        else:
            assert device.name == f'{db.get(Group, group).name} · Device {device.id}'
        assert db.get(DeviceScope, device.id).group_id == group
    code = response.text.split('<code class="activation">')[1].split('</code>')[0]
    activated = app.test_client().post('/api/device/activate', json={'activation_code': code})
    assert activated.status_code == 200
    assert activated.json['group_id'] == group


def test_overlong_name_preserves_form_and_does_not_reserve_a_slot(cloud):
    app, admin, group = activation_team(cloud)
    name = '<script>alert("label")</script>' + 'x' * 100
    with Session(app.extensions['cloud_engine']) as db:
        audit_count = len(db.scalars(select(AuditEvent)).all())
    response = post(admin, '/devices', group_id=group, name=name)
    assert response.status_code == 400
    assert 'Use 100 characters or fewer' in response.text
    assert '<code class="activation">' not in response.text
    assert name not in response.text  # Attempted text must be escaped in the attribute.
    form = ActivationForm(response.text)
    assert form.name['value'] == name
    assert form.name['aria-invalid'] == 'true'
    assert form.selected_groups == [str(group)]
    with Session(app.extensions['cloud_engine']) as db:
        assert db.scalar(select(Device)) is None
        assert len(db.scalars(select(AuditEvent)).all()) == audit_count
    fixed = post(admin, '/devices', group_id=group, name='Reception')
    assert fixed.status_code == 200
    assert '<code class="activation">' in fixed.text


def test_generated_device_name_fits_a_long_group_name(cloud):
    app, admin, group = activation_team(cloud)
    with Session(app.extensions['cloud_engine']) as db:
        db.get(Group, group).name = 'B' * 100
        db.commit()
    assert post(admin, '/devices', group_id=group).status_code == 200
    with Session(app.extensions['cloud_engine']) as db:
        device = db.scalar(select(Device))
        assert len(device.name) == 100
        assert device.name.endswith(f' · Device {device.id}')


def test_omitting_name_does_not_bypass_licence_caps(cloud):
    app, admin, group = activation_team(cloud)
    # The group's default allowance is one device.
    assert post(admin, '/devices', group_id=group).status_code == 200
    assert post(admin, '/devices', group_id=group).status_code == 409
    with Session(app.extensions['cloud_engine']) as db:
        assert len(db.scalars(select(Device)).all()) == 1
