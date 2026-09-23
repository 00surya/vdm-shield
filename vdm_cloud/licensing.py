"""Online licensing. Entitlements are provisioned by the service owner, not tenants."""
import hashlib
import hmac
import secrets
import time
from datetime import datetime, timezone
import click
from flask import abort, flash, jsonify, redirect, render_template, request, url_for
from itsdangerous import URLSafeTimedSerializer, BadSignature
from sqlalchemy import select, update, func, delete
from sqlalchemy.orm import Session
from .models import Device, Entitlement, Organisation, RenewalReceipt, Group, GroupLicence, DeviceScope, Membership
from .access import visible_groups, visible_devices, device_context, audit_event


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def register_licensing(app, engine, current_user):
    signer = URLSafeTimedSerializer(app.config['SECRET_KEY'], salt='device-lease-v1')
    app.jinja_env.filters['utcdate'] = lambda value: datetime.fromtimestamp(value, timezone.utc).strftime('%d %b %Y, %H:%M UTC')

    @app.cli.command('grant-licence')
    @click.option('--organisation-id', type=int, required=True)
    @click.option('--days', type=click.IntRange(1, 366), required=True)
    @click.option('--devices', type=click.IntRange(1, 1000), default=1)
    @click.option('--cameras', type=click.IntRange(1, 1000), default=4)
    def grant(organisation_id, days, devices, cameras):
        """Service-owner command: replace an organisation's entitlement from today."""
        with Session(engine) as db:
            if not db.get(Organisation, organisation_id):
                raise click.ClickException('Organisation not found.')
            reserved = db.scalar(select(func.count()).select_from(Device).where(Device.organisation_id == organisation_id,
                (Device.status == 'active') | ((Device.status == 'pending') & (Device.activation_expires > int(time.time())))))
            if reserved > devices:
                raise click.ClickException('Revoke surplus devices before reducing the device allowance.')
            entitlement = db.get(Entitlement, organisation_id)
            if not entitlement:
                entitlement = Entitlement(organisation_id=organisation_id)
                db.add(entitlement)
            entitlement.expires = int(time.time()) + days * 86400
            entitlement.device_limit = devices
            entitlement.camera_limit = cameras
            db.commit()
        click.echo('Entitlement saved. Renewal does not extend this expiry date.')

    def admin(db):
        user = current_user(db)
        if not user or user.role != 'admin':
            abort(403)
        return user

    def authenticated(db):
        user = current_user(db)
        if not user:
            abort(403)
        return user

    def render_devices(db, user, code=None, name_error=None):
        entitlement = db.get(Entitlement, user.organisation_id)
        groups = visible_groups(db, user)
        query = visible_devices(user)
        group_filter = request.args.get('group', type=int)
        if group_filter:
            if group_filter not in {g.id for g in groups}:
                abort(404)
            query = query.where(Device.id.in_(select(DeviceScope.device_id).where(DeviceScope.group_id == group_filter)))
        devices = db.scalars(query.order_by(Device.id.desc())).all()
        scopes = {d.id: db.get(DeviceScope, d.id) for d in devices}
        group_names = {g.id: g.name for g in groups}
        selected_group = request.form.get('group_id', type=int) if request.method == 'POST' else group_filter
        return render_template('devices.html', entitlement=entitlement, devices=devices,
                               organisation_id=user.organisation_id, now=int(time.time()), code=code,
                               user=user, groups=groups, scopes=scopes, group_names=group_names,
                               selected_group=selected_group, filtered_group=group_filter,
                               device_name=request.form.get('name', '') if name_error else '', name_error=name_error)

    @app.get('/devices')
    def devices():
        with Session(engine) as db:
            return render_devices(db, authenticated(db))

    @app.post('/devices')
    def issue_activation():
        with Session(engine) as db:
            user = authenticated(db)
            name = request.form.get('name', '').strip()
            if len(name) > 100:
                return render_devices(db, user, name_error='Use 100 characters or fewer for the device name.'), 400
            now = int(time.time())
            # A conditional write serializes allocations on SQLite and PostgreSQL.
            locked = db.execute(update(Entitlement).where(Entitlement.organisation_id == user.organisation_id,
                Entitlement.expires > now).values(expires=Entitlement.expires))
            if locked.rowcount != 1:
                abort(403, 'No active licence. Contact VDM to provision or renew your plan.')
            entitlement = db.get(Entitlement, user.organisation_id)
            raw_group = request.form.get('group_id')
            group = None
            if raw_group:
                group_id = request.form.get('group_id', type=int)
                group = db.get(Group, group_id) if group_id else None
                if not group or group.organisation_id != user.organisation_id:
                    abort(404)
                if user.role != 'admin' and not db.get(Membership, (user.id, group.id)):
                    abort(403, 'You can activate devices only for your assigned groups.')
                policy = db.get(GroupLicence, group.id)
                if not policy or not policy.enabled:
                    abort(403, 'This group has no enabled licence. Contact your administrator.')
                group_count = db.scalar(select(func.count()).select_from(Device).join(DeviceScope, DeviceScope.device_id == Device.id)
                    .where(DeviceScope.group_id == group.id,
                        (Device.status == 'active') | ((Device.status == 'pending') & (Device.activation_expires > now))))
                if group_count >= policy.device_limit:
                    abort(409, 'This group has used its device allowance. Ask your administrator to free or increase a slot.')
            elif user.role != 'admin':
                abort(403, 'Choose one of your assigned groups.')
            db.execute(update(Device).where(Device.organisation_id == user.organisation_id,
                Device.status == 'pending', Device.activation_expires <= now).values(status='expired', activation_hash=None))
            count = db.scalar(select(func.count()).select_from(Device).where(
                Device.organisation_id == user.organisation_id, Device.status.in_(['pending', 'active'])))
            if count >= entitlement.device_limit:
                abort(409, 'All device slots are reserved. Revoke an unused device or request a larger plan.')
            code = secrets.token_urlsafe(32)
            device = Device(organisation_id=user.organisation_id, name=name or 'Device',
                activation_hash=digest(code), activation_expires=now + 3600)
            db.add(device)
            db.flush()
            if not name:
                suffix = f' · Device {device.id}'
                name = (group.name if group else 'VDM')[:100 - len(suffix)].rstrip() + suffix
                device.name = name
            if group:
                db.add(DeviceScope(device_id=device.id, group_id=group.id, user_id=user.id))
            audit_event(db, user, 'device.code_created', f'{name} · {group.name if group else "Organisation"} · device {device.id}')
            db.commit()
            return render_devices(db, user, code)

    @app.post('/devices/<int:identity>/revoke')
    def revoke_device(identity):
        with Session(engine) as db:
            user = authenticated(db)
            device = db.scalar(visible_devices(user).where(Device.id == identity))
            if not device:
                abort(404)
            scope = db.get(DeviceScope, identity)
            if user.role != 'admin' and (not scope or scope.user_id != user.id):
                abort(403, 'Only an administrator or the person who enrolled this device may revoke it.')
            row = db.execute(update(Device).where(Device.id == identity, Device.organisation_id == user.organisation_id)
                .values(status='revoked', activation_hash=None, credential_hash=None))
            if row.rowcount != 1:
                abort(404)
            audit_event(db, user, 'device.revoked', f'{device.name} · device {identity}')
            db.commit()
        flash('Device revoked. Further licence checks and renewals are blocked.')
        return redirect(url_for('devices'))

    def failure(message, status=401):
        return jsonify(error=message), status

    def body():
        # These exact routes authenticate with secrets, never browser cookies.
        if request.headers.get('Origin') or not request.is_json:
            return None
        value = request.get_json(silent=True)
        return value if isinstance(value, dict) else None

    def lease(db, device, entitlement, credential):
        now = int(time.time())
        expires = min(now + 300, entitlement.expires)
        context = device_context(db, device, entitlement)
        if context is None:
            raise ValueError('Device authority must be checked before issuing a lease.')
        organisation = db.get(Organisation, device.organisation_id)
        payload = {'device_id': device.id, 'organisation_id': device.organisation_id,
                   'camera_limit': context['camera_limit'], 'group_id': context['group_id'], 'expires': expires,
                   'credential_hash': digest(credential)}
        return {'server_time': now, 'device_id': device.id, 'licence_expires': entitlement.expires,
                **context, 'organisation_id': device.organisation_id, 'organisation_name': organisation.name,
                'camera_limit': context['camera_limit'], 'valid_until': expires,
                'check_again_in_seconds': min(120, max(1, expires - now)),
                'lease': signer.dumps(payload)}

    @app.post('/api/device/activate')
    def activate_device():
        data = body()
        if data is None or set(data) != {'activation_code'} or not isinstance(data.get('activation_code'), str) or len(data['activation_code']) > 128:
            return failure('Send only activation_code as JSON, without an Origin header.', 400)
        now = int(time.time())
        with Session(engine) as db:
            device = db.scalar(select(Device).where(Device.activation_hash == digest(data['activation_code'])))
            if not device:
                return failure('Invalid or unavailable activation code.')
            entitlement = db.get(Entitlement, device.organisation_id)
            if not entitlement or entitlement.expires <= now:
                return failure('Organisation licence has expired.', 403)
            if device_context(db, device, entitlement) is None:
                return failure('Group licence or member access is no longer available.', 403)
            credential = secrets.token_urlsafe(48)
            claimed = db.execute(update(Device).where(Device.id == device.id, Device.status == 'pending',
                Device.activation_hash == digest(data['activation_code']), Device.activation_expires > now)
                .values(status='active', activation_hash=None, credential_hash=digest(credential), last_seen=now))
            if claimed.rowcount != 1:
                return failure('Invalid or unavailable activation code.')
            result = lease(db, device, entitlement, credential)
            db.commit()
        return jsonify(**result, refresh_token=credential)

    @app.post('/api/device/renew')
    def renew_device():
        data = body()
        if data is None or set(data) - {'request_id'} or ('request_id' in data and (not isinstance(data['request_id'], str) or not 20 <= len(data['request_id']) <= 128)):
            return failure('Send an empty JSON object without an Origin header.', 400)
        auth = request.headers.get('Authorization', '')
        if not auth.startswith('Bearer ') or len(auth) > 200:
            return failure('Device credential required.')
        credential = auth[7:]
        now = int(time.time())
        with Session(engine) as db:
            request_id = data.get('request_id')
            new_credential = hmac.new(app.config['SECRET_KEY'].encode(), ('renew:' + credential + ':' + request_id).encode(), hashlib.sha256).hexdigest() if request_id else secrets.token_urlsafe(48)
            receipt = db.get(RenewalReceipt, digest(credential))
            if receipt and request_id and receipt.expires > now and receipt.request_hash == digest(request_id):
                device = db.get(Device, receipt.device_id)
                entitlement = db.get(Entitlement, device.organisation_id) if device else None
                if device and device.status == 'active' and device.credential_hash == receipt.new_hash and device_context(db, device, entitlement):
                    return jsonify(**lease(db, device, entitlement, new_credential), refresh_token=new_credential)
                return failure('Credential already renewed or revoked.')
            device = db.scalar(select(Device).where(Device.credential_hash == digest(credential), Device.status == 'active'))
            if not device:
                return failure('Invalid or revoked device credential.')
            entitlement = db.get(Entitlement, device.organisation_id)
            if not entitlement or entitlement.expires <= now:
                return failure('Organisation licence has expired.', 403)
            if device_context(db, device, entitlement) is None:
                return failure('Group licence or member access is no longer available.', 403)
            changed = db.execute(update(Device).where(Device.id == device.id, Device.status == 'active',
                Device.credential_hash == digest(credential)).values(credential_hash=digest(new_credential), last_seen=now))
            if changed.rowcount != 1:
                return failure('Credential already renewed or revoked.')
            if request_id:
                db.execute(delete(RenewalReceipt).where(RenewalReceipt.expires <= now))
                db.add(RenewalReceipt(credential_hash=digest(credential), request_hash=digest(request_id), device_id=device.id, new_hash=digest(new_credential), expires=now + 600))
            result = lease(db, device, entitlement, new_credential)
            db.commit()
        return jsonify(**result, refresh_token=new_credential)

    @app.post('/api/device/check')
    def check_device():
        data = body()
        if data is None or set(data) != {'lease'} or not isinstance(data.get('lease'), str):
            return failure('Send only lease as JSON without an Origin header.', 400)
        try:
            payload = signer.loads(data['lease'], max_age=300)
        except BadSignature:
            return failure('Lease invalid or expired.')
        with Session(engine) as db:
            device = db.get(Device, payload['device_id'])
            entitlement = db.get(Entitlement, payload['organisation_id'])
            if not device or device.status != 'active' or device.credential_hash != payload['credential_hash'] or not entitlement or min(entitlement.expires, payload['expires']) <= time.time():
                return failure('Licence invalid, revoked or expired.')
            context = device_context(db, device, entitlement)
            if context is None:
                return failure('Group licence or member access is no longer available.', 403)
            return jsonify(valid=True, device_id=device.id, camera_limit=context['camera_limit'], group_id=context['group_id'],
                           valid_until=min(payload['expires'], entitlement.expires))
