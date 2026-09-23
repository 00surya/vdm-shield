"""Organisation accounts, isolated from local footage and inference."""
import hashlib
import os
import re
import secrets
import smtplib
import time
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from urllib.parse import urlsplit

from flask import Flask, flash, redirect, render_template, request, session, url_for, abort
from werkzeug.exceptions import HTTPException
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from sqlalchemy import create_engine, select, delete
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from werkzeug.security import generate_password_hash, check_password_hash
from .models import Base, Organisation, User, LoginSession, RateLimit, Group, Membership, Entitlement, TrialGrant, Invitation, AuditEvent, PasswordReset
from .access import visible_groups, visible_devices, audit_event
from .people import register_people
from .licensing import register_licensing
from .insights import register_insights
from .downloads import register_downloads


def create_app(config=None):
    app = Flask(__name__)
    app.config.from_mapping(
        SECRET_KEY=os.getenv('VDM_CLOUD_SECRET'),
        VDM_RELEASE_DIR=os.getenv('VDM_RELEASE_DIR'),
        DATABASE_URL=os.getenv('DATABASE_URL'),
        PUBLIC_URL=os.getenv('VDM_PUBLIC_URL', 'http://127.0.0.1:8766'),
        SESSION_COOKIE_NAME='vdm_cloud', SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE='Lax', SESSION_COOKIE_SECURE=True,
        PERMANENT_SESSION_LIFETIME=timedelta(hours=8), MAX_CONTENT_LENGTH=16384,
        SMTP_HOST=os.getenv('SMTP_HOST'), SMTP_PORT=int(os.getenv('SMTP_PORT', '587')),
        SMTP_USER=os.getenv('SMTP_USER'), SMTP_PASSWORD=os.getenv('SMTP_PASSWORD'),
        MAIL_FROM=os.getenv('MAIL_FROM'),
        TRIAL_DAYS=int(os.getenv('VDM_TRIAL_DAYS', '14')),
        TRIAL_DEVICES=2, TRIAL_CAMERAS=4,
        ALLOW_DEVELOPMENT_DOWNLOADS=False,
        DEVELOPMENT_EMAIL_PREVIEW=False,
    )
    if config:
        app.config.update(config)
    loopback_hosts = {'localhost', '127.0.0.1', '::1'}
    if app.config['DEVELOPMENT_EMAIL_PREVIEW'] and (
            urlsplit(app.config['PUBLIC_URL']).hostname not in loopback_hosts
            or not callable(app.config.get('MAIL_DELIVERY'))):
        raise RuntimeError('Development email preview requires a localhost URL and a development mail handler.')
    if not app.config['SECRET_KEY'] or len(app.config['SECRET_KEY']) < 32:
        raise RuntimeError('Set VDM_CLOUD_SECRET to a random secret of at least 32 characters.')
    database = app.config['DATABASE_URL']
    if not database:
        raise RuntimeError('Set DATABASE_URL. Use PostgreSQL in production.')
    if os.getenv('DYNO') and (database.startswith('sqlite') or not app.config['PUBLIC_URL'].startswith('https://')):
        raise RuntimeError('Heroku requires PostgreSQL and an HTTPS VDM_PUBLIC_URL.')
    if database.startswith(('postgres://', 'postgresql://')):
        database = 'postgresql+psycopg://' + database.split('://', 1)[1]
    engine = create_engine(database, pool_pre_ping=True)
    app.extensions['cloud_engine'] = engine
    signer = URLSafeTimedSerializer(app.config['SECRET_KEY'], salt='email-verification-v1')
    dummy_hash = generate_password_hash(secrets.token_urlsafe(32))

    def mail_configured():
        return callable(app.config.get('MAIL_DELIVERY')) or bool(
            app.config['SMTP_HOST'] and app.config['MAIL_FROM']
            and (not app.config['SMTP_USER'] or app.config['SMTP_PASSWORD']))

    @app.cli.command('init-db')
    def init_db():
        """Create initial account tables. Future schema changes require migrations."""
        Base.metadata.create_all(engine)
        print('Cloud account tables are ready.')

    def csrf():
        if 'csrf' not in session:
            session['csrf'] = secrets.token_urlsafe(32)
        return session['csrf']

    app.jinja_env.globals['csrf_token'] = csrf

    @app.before_request
    def protect():
        # The local launcher may expose verification links only to its local browser.
        # Neither forwarded headers nor a production request can enable this mode.
        if app.config['DEVELOPMENT_EMAIL_PREVIEW'] and (
                request.remote_addr not in {'127.0.0.1', '::1'}
                or urlsplit(request.host_url).hostname not in loopback_hosts):
            abort(403, 'The development account preview is available only on localhost.')
        if request.method == 'POST' and request.endpoint not in {'activate_device', 'renew_device', 'check_device', 'ingest_metadata'}:
            supplied = request.form.get('csrf', '')
            if not supplied or not secrets.compare_digest(supplied, session.get('csrf', '')):
                return 'This form expired. Reload the page and try again.', 400

    @app.after_request
    def headers(response):
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['Content-Security-Policy'] = "default-src 'self'; style-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'"
        if app.config['SESSION_COOKIE_SECURE']:
            response.headers['Strict-Transport-Security'] = 'max-age=31536000'
        return response

    def limited(action, email):
        # Database-backed counters work across workers. Do not trust client-supplied IP headers.
        key = hashlib.sha256(f'{action}:{email}'.encode()).hexdigest()
        now = int(time.time())
        with Session(engine) as db, db.begin():
            db.execute(delete(RateLimit).where(RateLimit.expires < now))
            row = db.get(RateLimit, key, with_for_update=True)
            if row is None:
                try:
                    with db.begin_nested():
                        db.add(RateLimit(key=key, count=1, expires=now + 900))
                        db.flush()
                    return False
                except IntegrityError:
                    row = db.get(RateLimit, key, with_for_update=True)
            row.count += 1
            return row.count > 10

    def current_user(db):
        token = session.get('login')
        if not token:
            return None
        saved = db.get(LoginSession, hashlib.sha256(token.encode()).hexdigest())
        if not saved or saved.expires <= time.time():
            return None
        user = db.get(User, saved.user_id)
        return user if user and user.verified and user.role in {'admin', 'operator'} else None

    @app.context_processor
    def workspace_context():
        with Session(engine) as db:
            user = current_user(db)
            org = db.get(Organisation, user.organisation_id) if user else None
            return dict(workspace_user={'email': user.email, 'role': user.role} if user else None,
                        workspace_name=org.name if org else None, trial_days=app.config['TRIAL_DAYS'],
                        development_email_preview=app.config['DEVELOPMENT_EMAIL_PREVIEW'],
                        email_delivery_configured=mail_configured(),
                        current_year=datetime.now(timezone.utc).year)

    @app.errorhandler(HTTPException)
    def request_error(error):
        if request.path.startswith('/api/'):
            return {'error': error.description}, error.code
        return render_template('error.html', error=error), error.code

    def verification_path(user):
        token = signer.dumps({'id': user.id, 'email': user.email})
        return url_for('verify', token=token)

    def send_verification(user):
        link = app.config['PUBLIC_URL'].rstrip('/') + verification_path(user)
        send_mail(user.email, link, 'Verify your VDM organisation account', 'Verify your email within one hour.')

    def remember_verification(email, user, password):
        session['verification_email'] = email
        session.pop('development_verification_user', None)
        # Retrying someone else's signup must never reveal their verification link.
        if (app.config['DEVELOPMENT_EMAIL_PREVIEW'] and user and not user.verified
                and check_password_hash(user.password_hash, password)):
            session['development_verification_user'] = user.id

    def verification_page(status=200):
        email = session.get('verification_email')
        if not email:
            return redirect(url_for('signup'))
        preview_url = None
        if app.config['DEVELOPMENT_EMAIL_PREVIEW']:
            user_id = session.get('development_verification_user')
            if user_id:
                with Session(engine) as db:
                    user = db.get(User, user_id)
                    if user and user.email == email and not user.verified:
                        preview_url = verification_path(user)
        return render_template('verify_email.html', email=email, preview_url=preview_url), status

    def send_mail(email, link, subject, explanation):
        delivery = app.config.get('MAIL_DELIVERY')
        if callable(delivery):
            delivery(email, link)
            return
        if not mail_configured():
            raise RuntimeError('Email delivery is not configured.')
        message = EmailMessage()
        message['From'] = app.config['MAIL_FROM']
        message['To'] = email
        message['Subject'] = subject
        message.set_content(f'{explanation}\n\n{link}\n\nIf you did not request this, ignore this message.')
        with smtplib.SMTP(app.config['SMTP_HOST'], app.config['SMTP_PORT'], timeout=15) as smtp:
            smtp.starttls()
            if app.config['SMTP_USER']:
                smtp.login(app.config['SMTP_USER'], app.config['SMTP_PASSWORD'])
            smtp.send_message(message)

    @app.get('/')
    def home():
        with Session(engine) as db:
            if current_user(db):
                return redirect(url_for('dashboard'))
        return render_template('home.html')

    @app.get('/product')
    def product():
        """Public product tour, also available to signed-in team members."""
        return render_template('home.html')

    @app.route('/signup', methods=['GET', 'POST'])
    def signup():
        # Do not create an unusable organisation when verification cannot be sent.
        if not mail_configured():
            return render_template('account.html', mode='signup'), 503 if request.method == 'POST' else 200
        if request.method == 'POST':
            email = request.form.get('email', '').strip().lower()
            name = request.form.get('organisation', '').strip()
            password = request.form.get('password', '')
            if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email) or len(email) > 254 or not 2 <= len(name) <= 160 or not 12 <= len(password) <= 128:
                flash('Enter an organisation name, a valid email and a password of 12–128 characters.')
                return render_template('account.html', mode='signup'), 400
            if limited('signup', email):
                return 'Too many requests. Try again in 15 minutes.', 429
            with Session(engine) as db:
                user = db.scalar(select(User).where(User.email == email))
                if user is None:
                    pending = db.scalar(select(Invitation).where(Invitation.email == email,
                        Invitation.status == 'pending', Invitation.expires > int(time.time())))
                    if pending:
                        flash('You have a team invitation. Use the invitation link in your email to join as an operator.')
                        return redirect(url_for('login'))
                    org = Organisation(name=name)
                    db.add(org)
                    db.flush()
                    user = User(organisation_id=org.id, email=email, password_hash=generate_password_hash(password))
                    db.add(user)
                    try:
                        db.commit()
                    except IntegrityError:
                        db.rollback()
                        user = db.scalar(select(User).where(User.email == email))
                # Unverified retries can resend mail without changing ownership or password.
                if user and not user.verified:
                    try:
                        send_verification(user)
                    except Exception:
                        app.logger.error('Verification email delivery failed; check SMTP configuration.')
                        flash('Email delivery is unavailable. Please try signup again later.')
                        return render_template('account.html', mode='signup'), 503
                remember_verification(email, user, password)
            return redirect(url_for('check_email'))
        return render_template('account.html', mode='signup')

    @app.get('/check-email')
    def check_email():
        return verification_page()

    @app.post('/resend-verification')
    def resend_verification():
        email = session.get('verification_email')
        if not email:
            return redirect(url_for('signup'))
        if not mail_configured():
            return verification_page(503)
        if limited('verification', email):
            flash('Too many verification requests. Please wait 15 minutes before trying again.')
            return verification_page(429)
        with Session(engine) as db:
            user = db.scalar(select(User).where(User.email == email))
            if user and not user.verified:
                try:
                    send_verification(user)
                except Exception:
                    app.logger.error('Verification email delivery failed; check SMTP configuration.')
                    flash('Email delivery is unavailable. Please try again later.')
                    return verification_page(503)
        flash('Verification instructions are ready below.' if app.config['DEVELOPMENT_EMAIL_PREVIEW']
              else 'If this address needs verification, a new email has been sent. Check your inbox and spam folder.')
        return redirect(url_for('check_email'))

    @app.route('/verify/<token>', methods=['GET', 'POST'])
    def verify(token):
        try:
            payload = signer.loads(token, max_age=3600)
        except (BadSignature, SignatureExpired):
            return 'Verification link expired or invalid. Submit signup again to request another.', 400
        if request.method == 'GET':
            return render_template('account.html', mode='verify')
        with Session(engine) as db:
            user = db.get(User, payload['id'])
            if not user or user.email != payload['email']:
                return 'Invalid verification link.', 400
            if not user.verified:
                password = request.form.get('password', '')
                if not 12 <= len(password) <= 128:
                    flash('Set a password of 12–128 characters to complete verification.')
                    return render_template('account.html', mode='verify'), 400
                user.password_hash = generate_password_hash(password)
                user.verified = True
                if (user.role == 'admin' and app.config['TRIAL_DAYS'] > 0
                        and not db.get(Entitlement, user.organisation_id) and not db.get(TrialGrant, user.organisation_id)):
                    now = int(time.time())
                    db.add(TrialGrant(organisation_id=user.organisation_id, granted_at=now))
                    db.add(Entitlement(organisation_id=user.organisation_id,
                        expires=now + app.config['TRIAL_DAYS'] * 86400,
                        device_limit=app.config['TRIAL_DEVICES'], camera_limit=app.config['TRIAL_CAMERAS']))
                    audit_event(db, user, 'trial.started', f'{app.config["TRIAL_DAYS"]}-day organisation trial')
                db.commit()
        session.pop('verification_email', None)
        session.pop('development_verification_user', None)
        flash('Email verified. You can now sign in.')
        return redirect(url_for('login'))

    @app.route('/login', methods=['GET', 'POST'])
    def login():
        if request.method == 'POST':
            email = request.form.get('email', '').strip().lower()[:254]
            if limited('login', email):
                return 'Too many attempts. Try again in 15 minutes.', 429
            with Session(engine) as db:
                user = db.scalar(select(User).where(User.email == email))
                valid = check_password_hash(user.password_hash if user else dummy_hash, request.form.get('password', '')[:129])
                if user and valid and not user.verified and user.role in {'admin', 'operator'}:
                    remember_verification(email, user, request.form.get('password', '')[:129])
                    flash('Finish verification before signing in.')
                    return verification_page(401)
                if not user or not valid or not user.verified or user.role not in {'admin', 'operator'}:
                    flash('Unable to sign in. Check your credentials and verify your email first.')
                    return render_template('account.html', mode='login'), 401
                token = secrets.token_urlsafe(32)
                db.execute(delete(LoginSession).where(LoginSession.expires < int(time.time())))
                db.add(LoginSession(token_hash=hashlib.sha256(token.encode()).hexdigest(), user_id=user.id, expires=int(time.time()) + 28800))
                db.commit()
                session.clear()
                session.permanent = True
                session['login'] = token
            return redirect(url_for('dashboard'))
        return render_template('account.html', mode='login')

    @app.post('/logout')
    def logout():
        token = session.get('login', '')
        with Session(engine) as db:
            db.execute(delete(LoginSession).where(LoginSession.token_hash == hashlib.sha256(token.encode()).hexdigest()))
            db.commit()
        session.clear()
        return redirect(url_for('login'))

    @app.get('/dashboard')
    def dashboard():
        with Session(engine) as db:
            user = current_user(db)
            if not user:
                return redirect(url_for('login'))
            org = db.get(Organisation, user.organisation_id)
            groups = visible_groups(db, user)
            devices = db.scalars(visible_devices(user)).all()
            entitlement = db.get(Entitlement, user.organisation_id)
            return render_template('dashboard.html', user=user, organisation=org, groups=groups,
                devices=devices, entitlement=entitlement, now=int(time.time()),
                member_count=len(db.scalars(select(User.id).where(User.organisation_id == user.organisation_id,
                    User.role.in_(['admin', 'operator']))).all()) if user.role == 'admin' else None)

    @app.get('/activity')
    def activity():
        with Session(engine) as db:
            user = current_user(db)
            if not user or user.role != 'admin':
                abort(403)
            events = db.scalars(select(AuditEvent).where(AuditEvent.organisation_id == user.organisation_id)
                .order_by(AuditEvent.id.desc()).limit(100)).all()
            return render_template('activity.html', events=events)

    @app.route('/forgot-password', methods=['GET', 'POST'])
    def forgot_password():
        # Apply the same result to every address, without revealing account state.
        if not mail_configured():
            return render_template('password.html', mode='request'), 503 if request.method == 'POST' else 200
        if request.method == 'POST':
            email = request.form.get('email', '').strip().lower()[:254]
            if limited('password-reset', email):
                abort(429, 'Too many requests. Try again in 15 minutes.')
            with Session(engine) as db:
                user = db.scalar(select(User).where(User.email == email, User.verified.is_(True),
                    User.role.in_(['admin', 'operator'])))
                if user:
                    token = secrets.token_urlsafe(32)
                    db.add(PasswordReset(token_hash=hashlib.sha256(token.encode()).hexdigest(), user_id=user.id,
                        expires=int(time.time()) + 3600))
                    db.commit()
                    try:
                        send_mail(email, app.config['PUBLIC_URL'].rstrip('/') + url_for('reset_password', token=token),
                            'Reset your VDM password', 'This password reset link expires in one hour.')
                    except Exception:
                        app.logger.error('Password reset email delivery failed.')
            flash('If an active account matches that email, a password reset link has been sent.')
            return redirect(url_for('login'))
        return render_template('password.html', mode='request')

    @app.route('/reset-password/<token>', methods=['GET', 'POST'])
    def reset_password(token):
        from sqlalchemy import update
        with Session(engine) as db:
            reset = db.get(PasswordReset, hashlib.sha256(token.encode()).hexdigest())
            if not reset or reset.used or reset.expires <= time.time():
                abort(400, 'This reset link expired or was already used. Request a new link.')
            if request.method == 'POST':
                password = request.form.get('password', '')
                if not 12 <= len(password) <= 128:
                    abort(400, 'Choose a password of 12–128 characters.')
                claimed = db.execute(update(PasswordReset).where(PasswordReset.token_hash == reset.token_hash,
                    PasswordReset.used.is_(False), PasswordReset.expires > int(time.time())).values(used=True))
                if claimed.rowcount != 1:
                    abort(400, 'This reset link was already used.')
                user = db.get(User, reset.user_id)
                if not user or user.role not in {'admin', 'operator'}:
                    abort(400, 'Account unavailable. Contact your administrator.')
                user.password_hash = generate_password_hash(password)
                db.execute(delete(LoginSession).where(LoginSession.user_id == user.id))
                db.execute(update(PasswordReset).where(PasswordReset.user_id == user.id).values(used=True))
                audit_event(db, user, 'password.reset', 'Password changed; all website sessions ended')
                db.commit()
                flash('Password updated. Sign in with your new password.')
                return redirect(url_for('login'))
        return render_template('password.html', mode='reset')

    @app.get('/health')
    def health():
        return {'service': 'VDM accounts', 'status': 'ok'}

    register_people(app, engine, current_user, send_mail, limited)
    register_licensing(app, engine, current_user)
    register_insights(app, engine, current_user)
    register_downloads(app, engine, current_user)
    return app
