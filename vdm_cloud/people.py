"""Administrator-only people management and email-bound invitation acceptance."""
import hashlib
import re
import secrets
import time
from flask import abort, flash, redirect, render_template, request, url_for
from sqlalchemy import select, delete, update, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from werkzeug.security import generate_password_hash
from .models import Group, Membership, Invitation, User, LoginSession, GroupLicence, Entitlement, Device, DeviceScope
from .access import audit_event, visible_groups, revoke_user_devices


def register_people(app, engine, current_user, send_mail, limited):
    def admin(db):
        user = current_user(db)
        if not user or user.role != 'admin':
            abort(403)
        return user

    def owned(db, model, identity, organisation_id):
        row = db.get(model, identity)
        if not row or row.organisation_id != organisation_id:
            abort(404)
        return row

    @app.get('/groups')
    def groups():
        with Session(engine) as db:
            user = current_user(db)
            if not user:
                return redirect(url_for('login'))
            rows = []
            for group in visible_groups(db, user):
                policy = db.get(GroupLicence, group.id)
                members = db.scalars(select(User).join(Membership, Membership.user_id == User.id)
                    .where(Membership.group_id == group.id, User.organisation_id == user.organisation_id)).all()
                count = db.scalar(select(func.count()).select_from(Device).join(DeviceScope, DeviceScope.device_id == Device.id)
                    .where(DeviceScope.group_id == group.id, Device.status.in_(['active', 'pending']),
                        (Device.status == 'active') | (Device.activation_expires > int(time.time()))))
                rows.append(dict(group=group, policy=policy, members=members, devices=count))
            return render_template('groups.html', groups=rows, user=user,
                entitlement=db.get(Entitlement, user.organisation_id))

    @app.get('/people')
    def people():
        with Session(engine) as db:
            user = admin(db)
            groups = db.scalars(select(Group).where(Group.organisation_id == user.organisation_id).order_by(Group.name)).all()
            users = db.scalars(select(User).where(User.organisation_id == user.organisation_id).order_by(User.email)).all()
            invitations = db.scalars(select(Invitation).where(Invitation.organisation_id == user.organisation_id).order_by(Invitation.id.desc())).all()
            memberships = {u.id: set(db.scalars(select(Membership.group_id).where(Membership.user_id == u.id))) for u in users}
            return render_template('people.html', groups=groups, users=users, invitations=invitations, memberships=memberships, now=int(time.time()))

    @app.post('/groups')
    def create_group():
        with Session(engine) as db:
            user = admin(db)
            name = request.form.get('name', '').strip()
            if not 2 <= len(name) <= 100:
                abort(400, 'Group names must contain 2–100 characters.')
            existing = db.scalar(select(Group).where(Group.organisation_id == user.organisation_id, Group.name == name))
            if existing:
                flash('A group with that name already exists.')
            else:
                group = Group(organisation_id=user.organisation_id, name=name)
                db.add(group)
                db.flush()
                entitlement = db.get(Entitlement, user.organisation_id)
                db.add(GroupLicence(group_id=group.id,
                    device_limit=entitlement.device_limit if entitlement else 1,
                    camera_limit=min(4, entitlement.camera_limit) if entitlement else 4))
                audit_event(db, user, 'group.created', name)
                db.commit()
                flash('Group created. You can now invite operators to it.')
        return redirect(url_for('groups'))

    @app.post('/groups/<int:identity>/licence')
    def group_licence(identity):
        with Session(engine) as db:
            user = admin(db)
            group = owned(db, Group, identity, user.organisation_id)
            # Same organisation row lock used by device allocations.
            db.execute(update(Entitlement).where(Entitlement.organisation_id == user.organisation_id)
                .values(expires=Entitlement.expires))
            entitlement = db.get(Entitlement, user.organisation_id)
            devices = request.form.get('device_limit', type=int)
            cameras = request.form.get('camera_limit', type=int)
            if not entitlement or not devices or not cameras or not 1 <= devices <= entitlement.device_limit or not 1 <= cameras <= min(4, entitlement.camera_limit):
                abort(400, 'Group limits must fit within your organisation plan (up to four cameras per device).')
            count = db.scalar(select(func.count()).select_from(Device).join(DeviceScope, DeviceScope.device_id == Device.id)
                .where(DeviceScope.group_id == group.id,
                    (Device.status == 'active') | ((Device.status == 'pending') & (Device.activation_expires > int(time.time())))))
            if count > devices:
                abort(409, 'Revoke surplus group devices before reducing its device limit.')
            policy = db.get(GroupLicence, group.id)
            if not policy:
                policy = GroupLicence(group_id=group.id)
                db.add(policy)
            policy.device_limit, policy.camera_limit = devices, cameras
            policy.enabled = request.form.get('enabled') == 'yes'
            audit_event(db, user, 'group.licence_changed', f'{group.name}: {devices} devices, {cameras} cameras/device, enabled={policy.enabled}')
            db.commit()
        flash('Group licence updated. Connected devices apply changes on their next licence check.')
        return redirect(url_for('groups'))

    @app.post('/invitations')
    def invite():
        with Session(engine) as db:
            user = admin(db)
            email = request.form.get('email', '').strip().lower()
            if len(email) > 254 or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email):
                abort(400, 'Enter a valid email address.')
            group = owned(db, Group, request.form.get('group_id', type=int), user.organisation_id)
            if limited('invite', str(user.organisation_id)):
                abort(429, 'Too many invitations. Try again in 15 minutes.')
            existing = db.scalar(select(User).where(User.email == email))
            if existing:
                if existing.organisation_id == user.organisation_id and existing.role in {'operator', 'suspended'}:
                    if not db.get(Membership, (existing.id, group.id)):
                        db.add(Membership(user_id=existing.id, group_id=group.id))
                        audit_event(db, user, 'membership.added', f'{email} → {group.name}')
                        db.commit()
                    flash('Existing team member added to this group. Their other groups are unchanged.')
                else:
                    flash('This account cannot be added through an invitation. Contact the account administrator.')
                return redirect(url_for('people'))
            token = secrets.token_urlsafe(32)
            invitation = Invitation(organisation_id=user.organisation_id, group_id=group.id, email=email,
                                    token_hash=hashlib.sha256(token.encode()).hexdigest(), expires=int(time.time()) + 86400)
            db.add(invitation)
            audit_event(db, user, 'invitation.created', f'{email} → {group.name}')
            db.commit()
            try:
                link = app.config['PUBLIC_URL'].rstrip('/') + url_for('accept_invitation', token=token)
                send_mail(email, link, 'You are invited to VDM', 'Accept your operator invitation within 24 hours.')
            except Exception:
                invitation.status = 'delivery_failed'
                db.commit()
                flash('Invitation could not be delivered. Check email delivery, then send a new invitation.')
                return redirect(url_for('people'))
            flash('Invitation sent. The operator will choose their own password.')
        return redirect(url_for('people'))

    @app.post('/invitations/<int:identity>/revoke')
    def revoke_invitation(identity):
        with Session(engine) as db:
            user = admin(db)
            owned(db, Invitation, identity, user.organisation_id)
            db.execute(update(Invitation).where(Invitation.id == identity, Invitation.status == 'pending').values(status='revoked'))
            audit_event(db, user, 'invitation.revoked', f'Invitation {identity}')
            db.commit()
        flash('Invitation is no longer active.')
        return redirect(url_for('people'))

    @app.route('/join/<token>', methods=['GET', 'POST'])
    def accept_invitation(token):
        digest = hashlib.sha256(token.encode()).hexdigest()
        with Session(engine) as db:
            invitation = db.scalar(select(Invitation).where(Invitation.token_hash == digest))
            if not invitation or invitation.status != 'pending' or invitation.expires <= time.time():
                abort(400, 'Invitation expired or unavailable. Ask your administrator for a new one.')
            if request.method == 'GET':
                return render_template('join.html', invitation=invitation)
            password = request.form.get('password', '')
            if not 12 <= len(password) <= 128:
                abort(400, 'Choose a password of 12–128 characters.')
            # Conditional claim prevents replay, including simultaneous acceptance.
            claimed = db.execute(update(Invitation).where(Invitation.id == invitation.id,
                Invitation.status == 'pending', Invitation.expires > int(time.time())).values(status='accepted'))
            if claimed.rowcount != 1:
                abort(400, 'Invitation is no longer active.')
            user = User(organisation_id=invitation.organisation_id, email=invitation.email,
                        password_hash=generate_password_hash(password), verified=True, role='operator')
            db.add(user)
            try:
                db.flush()
                db.add(Membership(user_id=user.id, group_id=invitation.group_id))
                audit_event(db, user, 'invitation.accepted', f'Joined group {invitation.group_id}')
                db.commit()
            except IntegrityError:
                db.rollback()
                abort(409, 'This email already has an account. Sign in or contact your administrator.')
        flash('Invitation accepted. Sign in with your new password.')
        return redirect(url_for('login'))

    @app.post('/people/<int:identity>/access')
    def change_access(identity):
        with Session(engine) as db:
            actor = admin(db)
            target = owned(db, User, identity, actor.organisation_id)
            if target.role not in {'operator', 'suspended'}:
                abort(400, 'Administrator access cannot be changed here.')
            action = request.form.get('action')
            if action not in {'suspend', 'restore'}:
                abort(400)
            target.role = 'suspended' if action == 'suspend' else 'operator'
            db.execute(delete(LoginSession).where(LoginSession.user_id == target.id))
            if action == 'suspend':
                revoke_user_devices(db, target.id)
            audit_event(db, actor, 'member.' + action, target.email)
            db.commit()
        flash('Access suspended; website sessions and this operator’s device activations revoked.' if action == 'suspend' else 'Access restored. The operator can sign in and create new activation codes.')
        return redirect(url_for('people'))

    @app.post('/people/<int:identity>/groups')
    def change_groups(identity):
        with Session(engine) as db:
            actor = admin(db)
            target = owned(db, User, identity, actor.organisation_id)
            if target.role not in {'operator', 'suspended'}:
                abort(400)
            try:
                group_ids = {int(value) for value in request.form.getlist('group_id')}
            except ValueError:
                abort(400)
            for group_id in group_ids:
                owned(db, Group, group_id, actor.organisation_id)
            db.execute(delete(Membership).where(Membership.user_id == target.id))
            db.add_all(Membership(user_id=target.id, group_id=group_id) for group_id in group_ids)
            revoke_user_devices(db, target.id, keep_groups=group_ids)
            audit_event(db, actor, 'membership.changed', f'{target.email}: groups {sorted(group_ids)}')
            db.commit()
        flash('Group access saved.')
        return redirect(url_for('people'))
