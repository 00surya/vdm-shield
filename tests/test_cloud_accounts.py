import re
from urllib.parse import urlsplit

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from vdm_cloud.app import create_app
from vdm_cloud.models import Base, User, Organisation, PasswordReset

@pytest.fixture
def cloud(tmp_path):
    mail = []
    app = create_app({'TESTING': True, 'SECRET_KEY': 'x' * 48,
        'DATABASE_URL': f'sqlite:///{tmp_path / "cloud.sqlite"}',
        'SESSION_COOKIE_SECURE': False, 'TRIAL_DAYS': 0, 'MAIL_DELIVERY': lambda email, link: mail.append((email, link))})
    Base.metadata.create_all(app.extensions['cloud_engine'])
    yield app, mail
    app.extensions['cloud_engine'].dispose()


def post(client, path, **data):
    client.get('/login')
    with client.session_transaction() as cookie:
        data['csrf'] = cookie['csrf']
    return client.post(path, data=data)


def register(client, mail, email='admin@first.test', organisation='First organisation'):
    response = post(client, '/signup', email=email, organisation=organisation, password='a long secure password')
    assert response.status_code == 302
    path = mail[-1][1].split('8766')[1]
    assert client.get(path).status_code == 200
    assert post(client, path, password='a long secure password').status_code == 302


def test_verified_signup_login_and_revoked_logout(cloud):
    app, mail = cloud
    client = app.test_client()
    assert client.get('/dashboard').status_code == 302
    post(client, '/signup', email='admin@first.test', organisation='First organisation', password='a long secure password')
    assert post(client, '/login', email='admin@first.test', password='a long secure password').status_code == 401
    path = mail[-1][1].split('8766')[1]
    post(client, path, password='a long secure password')
    assert post(client, '/login', email='admin@first.test', password='a long secure password').status_code == 302
    assert b'First organisation' in client.get('/dashboard').data
    old_cookie = client.get_cookie('vdm_cloud').value
    assert post(client, '/logout').status_code == 302
    client.set_cookie('vdm_cloud', old_cookie)
    assert client.get('/dashboard').status_code == 302


def test_organisation_isolation_and_duplicate_signup(cloud):
    app, mail = cloud
    first, second = app.test_client(), app.test_client()
    register(first, mail)
    register(second, mail, 'admin@second.test', 'Second organisation')
    post(first, '/login', email='admin@first.test', password='a long secure password')
    response = first.get('/dashboard?organisation_id=2')
    assert b'First organisation' in response.data
    assert b'Second organisation' not in response.data
    post(second, '/signup', email='admin@first.test', organisation='Hijacked', password='a different password')
    with Session(app.extensions['cloud_engine']) as db:
        assert len(db.scalars(select(User)).all()) == 2
    assert post(second, '/login', email='admin@first.test', password='a different password').status_code == 401


def test_csrf_invalid_token_rate_limit_and_no_media_routes(cloud):
    app, mail = cloud
    client = app.test_client()
    assert client.post('/signup', data={}).status_code == 400
    assert client.get('/verify/invalid').status_code == 400
    for _ in range(10):
        assert post(client, '/login', email='nobody@example.test', password='invalid').status_code == 401
    assert post(client, '/login', email='nobody@example.test', password='invalid').status_code == 429
    assert client.get('/api/video').status_code == 404


def test_failed_mail_can_retry(cloud):
    app, mail = cloud
    client = app.test_client()
    delivery = app.config['MAIL_DELIVERY']
    app.config['MAIL_DELIVERY'] = lambda *args: (_ for _ in ()).throw(RuntimeError('offline'))
    assert post(client, '/signup', email='admin@first.test', organisation='First', password='a long secure password').status_code == 503
    app.config['MAIL_DELIVERY'] = delivery
    register(client, mail)


def test_unconfigured_mail_blocks_signup_before_creating_accounts_and_recovers(cloud):
    app, mail = cloud
    client = app.test_client()
    delivery = app.config.pop('MAIL_DELIVERY')
    app.config.update(SMTP_HOST=None, SMTP_USER=None, SMTP_PASSWORD=None, MAIL_FROM=None)
    page = client.get('/signup')
    assert page.status_code == 200
    assert b'Registration is temporarily unavailable' in page.data
    assert b'name="organisation"' not in page.data
    assert post(client, '/signup', email='setup@example.test', organisation='Setup',
                password='a long secure password').status_code == 503
    with Session(app.extensions['cloud_engine']) as db:
        assert db.scalar(select(User)) is None
        assert db.scalar(select(Organisation)) is None
    assert not mail
    app.config['MAIL_DELIVERY'] = delivery
    register(client, mail)


def test_missing_smtp_password_is_not_treated_as_configured(cloud):
    app, _ = cloud
    app.config.pop('MAIL_DELIVERY')
    app.config.update(SMTP_HOST='smtp.example.test', MAIL_FROM='accounts@example.test',
                      SMTP_USER='mailer', SMTP_PASSWORD=None)
    assert b'Registration is temporarily unavailable' in app.test_client().get('/signup').data


def test_unconfigured_mail_preserves_login_and_blocks_reset_without_side_effects(cloud):
    app, mail = cloud
    client = app.test_client()
    register(client, mail)
    app.config.pop('MAIL_DELIVERY')
    app.config.update(SMTP_HOST=None, SMTP_USER=None, SMTP_PASSWORD=None, MAIL_FROM=None)
    assert post(client, '/login', email='admin@first.test', password='a long secure password').status_code == 302
    assert b'Password reset emails are temporarily unavailable' in client.get('/forgot-password').data
    for email in ['admin@first.test', 'missing@example.test']:
        response = post(client, '/forgot-password', email=email)
        assert response.status_code == 503
        assert b'Password reset emails are temporarily unavailable' in response.data
    with Session(app.extensions['cloud_engine']) as db:
        assert db.scalar(select(PasswordReset)) is None


def test_pending_verification_does_not_claim_mail_was_sent_when_unconfigured(cloud):
    app, mail = cloud
    client = app.test_client()
    post(client, '/signup', email='pending@example.test', organisation='Pending', password='a long secure password')
    delivery = app.config.pop('MAIL_DELIVERY')
    app.config.update(SMTP_HOST=None, SMTP_USER=None, SMTP_PASSWORD=None, MAIL_FROM=None)
    page = client.get('/check-email')
    assert b'Verification is temporarily unavailable' in page.data
    assert b'Resend verification email' not in page.data
    assert b'href="/verify/' not in page.data
    assert post(client, '/resend-verification').status_code == 503
    assert len(mail) == 1
    with Session(app.extensions['cloud_engine']) as db:
        assert not db.scalar(select(User).where(User.email == 'pending@example.test')).verified
    app.config['MAIL_DELIVERY'] = delivery
    assert post(client, '/resend-verification').status_code == 302
    assert len(mail) == 2


def test_verification_replaces_preregistered_password(cloud):
    app, mail = cloud
    client = app.test_client()
    post(client, '/signup', email='owner@site.test', organisation='Site', password='attacker chosen password')
    path = mail[-1][1].split('8766')[1]
    assert post(client, path, password='owner chosen password').status_code == 302
    assert post(client, '/login', email='owner@site.test', password='attacker chosen password').status_code == 401
    assert post(client, '/login', email='owner@site.test', password='owner chosen password').status_code == 302
    # A previously used verification link cannot reset a verified account password.
    post(client, path, password='another chosen password')
    assert post(client, '/login', email='owner@site.test', password='another chosen password').status_code == 401


def test_signup_has_verification_step_and_resend_without_exposing_link(cloud):
    app, mail = cloud
    client = app.test_client()
    response = post(client, '/signup', email='setup@example.test', organisation='Setup', password='a long secure password')
    assert response.headers['Location'] == '/check-email'
    page = client.get('/check-email')
    assert b'Check your email.' in page.data
    assert b'Resend verification email' in page.data
    assert b'href="/verify/' not in page.data
    assert client.post('/resend-verification').status_code == 400
    assert post(client, '/resend-verification').status_code == 302
    assert len(mail) == 2
    app.config['MAIL_DELIVERY'] = lambda *args: (_ for _ in ()).throw(RuntimeError('offline'))
    response = post(client, '/resend-verification')
    assert response.status_code == 503
    assert b'Email delivery is unavailable' in response.data


def test_local_signup_verification_and_login_roundtrip(cloud):
    app, mail = cloud
    app.config['DEVELOPMENT_EMAIL_PREVIEW'] = True
    client = app.test_client()
    assert b'Local preview: no email is sent' in client.get('/signup').data
    response = post(client, '/signup', email='local@example.test', organisation='Local workspace', password='a long secure password')
    page = client.get(response.headers['Location'])
    assert b'This local preview does not send email' in page.data
    path = re.search(rb'href="(/verify/[^"]+)"', page.data).group(1).decode()
    assert client.get('/dashboard').status_code == 302
    assert post(client, path, password='my chosen sign-in password').status_code == 302
    with client.session_transaction() as cookie:
        assert 'verification_email' not in cookie
        assert 'development_verification_user' not in cookie
    assert post(client, '/login', email='local@example.test', password='my chosen sign-in password').headers['Location'] == '/dashboard'
    assert b'Local workspace' in client.get('/dashboard').data


def test_existing_unverified_local_account_can_continue_from_login(cloud):
    app, mail = cloud
    first = app.test_client()
    post(first, '/signup', email='pending@example.test', organisation='Pending', password='a long secure password')
    # This account predates the visible local verification step.
    app.config['DEVELOPMENT_EMAIL_PREVIEW'] = True
    client = app.test_client()
    response = post(client, '/login', email='pending@example.test', password='a long secure password')
    assert response.status_code == 401
    assert b'Finish verification before signing in.' in response.data
    assert b'Continue verification' in response.data
    assert client.get('/dashboard').status_code == 302
    app.config['DEVELOPMENT_EMAIL_PREVIEW'] = False
    assert b'href="/verify/' not in client.get('/check-email').data


def test_local_preview_never_reveals_another_pending_account_link(cloud):
    app, mail = cloud
    app.config['DEVELOPMENT_EMAIL_PREVIEW'] = True
    owner, stranger = app.test_client(), app.test_client()
    post(owner, '/signup', email='owner@example.test', organisation='Owner', password='owner original password')
    response = post(stranger, '/signup', email='owner@example.test', organisation='Other', password='a different password')
    assert b'href="/verify/' not in stranger.get(response.headers['Location']).data
    post(stranger, '/resend-verification')
    assert b'href="/verify/' not in stranger.get('/check-email').data
    response = post(stranger, '/login', email='owner@example.test', password='a different password')
    assert response.status_code == 401
    assert b'href="/verify/' not in response.data
    assert stranger.get('/dashboard').status_code == 302
    path = urlsplit(mail[0][1]).path
    post(owner, path, password='owner final password')
    assert b'href="/verify/' not in owner.get('/check-email').data


def test_local_email_preview_rejects_non_local_requests(cloud):
    app, mail = cloud
    app.config['DEVELOPMENT_EMAIL_PREVIEW'] = True
    client = app.test_client()
    assert client.get('/signup', environ_overrides={'REMOTE_ADDR': '198.51.100.20'}).status_code == 403
    assert client.get('/signup', base_url='https://public.example.test').status_code == 403
    assert client.get('/signup', headers={'X-Forwarded-For': '127.0.0.1'},
                      environ_overrides={'REMOTE_ADDR': '198.51.100.20'}).status_code == 403
    with pytest.raises(RuntimeError, match='localhost URL'):
        create_app({'DEVELOPMENT_EMAIL_PREVIEW': True, 'PUBLIC_URL': 'https://public.example.test',
                    'MAIL_DELIVERY': lambda *args: None})


def test_verification_resend_is_limited_and_keeps_account_unverified(cloud):
    app, mail = cloud
    client = app.test_client()
    post(client, '/signup', email='limited@example.test', organisation='Limited', password='a long secure password')
    for _ in range(10):
        assert post(client, '/resend-verification').status_code == 302
    assert post(client, '/resend-verification').status_code == 429
    assert len(mail) == 11
    with Session(app.extensions['cloud_engine']) as db:
        user = db.scalar(select(User).where(User.email == 'limited@example.test'))
        assert not user.verified
