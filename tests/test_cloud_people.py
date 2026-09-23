import time
from urllib.parse import urlsplit
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_cloud_accounts import cloud, post, register
from vdm_cloud.models import Group, Invitation, User, Membership


def setup_team(app, mail, email='admin@first.test'):
    client = app.test_client()
    register(client, mail, email)
    post(client, '/login', email=email, password='a long secure password')
    assert post(client, '/groups', name='Front gate').status_code == 302
    with Session(app.extensions['cloud_engine']) as db:
        user = db.scalar(select(User).where(User.email == email))
        group = db.scalar(select(Group).where(Group.organisation_id == user.organisation_id))
        return client, group.id


def invite_operator(admin, mail, group_id, email='operator@first.test'):
    assert post(admin, '/invitations', email=email, group_id=group_id).status_code == 302
    return urlsplit(mail[-1][1]).path


def test_invite_accept_permissions_groups_and_suspend(cloud):
    app, mail = cloud
    admin, group = setup_team(app, mail)
    path = invite_operator(admin, mail, group)
    operator = app.test_client()
    assert operator.get(path).status_code == 200
    assert post(operator, path, password='operator secure password', role='admin').status_code == 302
    assert post(operator, path, password='replacement password').status_code == 400
    assert post(operator, '/login', email='operator@first.test', password='operator secure password').status_code == 302
    assert b'Front gate' in operator.get('/dashboard').data
    assert operator.get('/people').status_code == 403
    assert post(operator, '/groups', name='Forbidden').status_code == 403
    assert post(operator, '/invitations', email='new@first.test', group_id=group).status_code == 403
    with Session(app.extensions['cloud_engine']) as db:
        user = db.scalar(select(User).where(User.email == 'operator@first.test'))
        identity = user.id
        assert user.role == 'operator'
    assert post(admin, f'/people/{identity}/groups').status_code == 302
    assert b'No groups assigned' in operator.get('/dashboard').data
    assert post(admin, f'/people/{identity}/groups', group_id=str(group)).status_code == 302
    assert b'Front gate' in operator.get('/dashboard').data
    old_cookie = operator.get_cookie('vdm_cloud').value
    assert post(admin, f'/people/{identity}/access', action='suspend').status_code == 302
    assert operator.get('/dashboard').status_code == 302
    assert post(operator, '/login', email='operator@first.test', password='operator secure password').status_code == 401
    post(admin, f'/people/{identity}/access', action='restore')
    operator.set_cookie('vdm_cloud', old_cookie)
    assert operator.get('/dashboard').status_code == 302
    assert post(operator, '/login', email='operator@first.test', password='operator secure password').status_code == 302
    assert admin.get('/people').status_code == 200


def test_cross_organisation_access_rejected(cloud):
    app, mail = cloud
    first, group = setup_team(app, mail)
    second, other_group = setup_team(app, mail, 'admin@second.test')
    assert post(first, '/invitations', email='bad@first.test', group_id=other_group).status_code == 404
    path = invite_operator(second, mail, other_group, 'operator@second.test')
    operator = app.test_client()
    post(operator, path, password='operator secure password')
    with Session(app.extensions['cloud_engine']) as db:
        user = db.scalar(select(User).where(User.email == 'operator@second.test'))
        invitation = db.scalar(select(Invitation).where(Invitation.email == user.email))
        identity, invitation_id = user.id, invitation.id
    assert post(first, f'/people/{identity}/access', action='suspend').status_code == 404
    assert post(first, f'/people/{identity}/groups', group_id=group).status_code == 404
    assert post(second, f'/people/{identity}/groups', group_id=group).status_code == 404
    assert post(first, f'/invitations/{invitation_id}/revoke').status_code == 404
    assert b'operator@second.test' not in first.get('/people').data


def test_invitation_expiry_revocation_mail_failure_csrf(cloud):
    app, mail = cloud
    admin, group = setup_team(app, mail)
    path = invite_operator(admin, mail, group)
    with Session(app.extensions['cloud_engine']) as db:
        invitation = db.scalar(select(Invitation))
        identity = invitation.id
    assert admin.post(f'/invitations/{identity}/revoke').status_code == 400
    assert post(admin, f'/invitations/{identity}/revoke').status_code == 302
    assert app.test_client().get(path).status_code == 400
    path = invite_operator(admin, mail, group)
    with Session(app.extensions['cloud_engine']) as db:
        invitation = db.scalar(select(Invitation).order_by(Invitation.id.desc()))
        invitation.expires = int(time.time()) - 1
        db.commit()
    assert app.test_client().get(path).status_code == 400
    app.config['MAIL_DELIVERY'] = lambda *args: (_ for _ in ()).throw(RuntimeError('mail offline'))
    post(admin, '/invitations', email='other@first.test', group_id=group)
    with Session(app.extensions['cloud_engine']) as db:
        invitation = db.scalar(select(Invitation).where(Invitation.email == 'other@first.test'))
        assert invitation.status == 'delivery_failed'
    assert b'could not be delivered' in admin.get('/people').data
