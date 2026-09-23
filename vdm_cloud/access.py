"""Shared authorization rules for group devices, leases, downloads and telemetry."""
import time
from sqlalchemy import select, update
from .models import AuditEvent, Device, DeviceScope, Group, GroupLicence, Membership, User


def audit_event(db, user, action, detail):
    db.add(AuditEvent(organisation_id=user.organisation_id, actor_email=user.email,
        action=action, detail=detail[:1000], created_at=int(time.time())))


def visible_groups(db, user):
    query = select(Group).where(Group.organisation_id == user.organisation_id)
    if user.role != 'admin':
        query = query.join(Membership, Membership.group_id == Group.id).where(Membership.user_id == user.id)
    return db.scalars(query.order_by(Group.name)).all()


def visible_devices(user):
    query = select(Device).where(Device.organisation_id == user.organisation_id)
    if user.role != 'admin':
        query = query.join(DeviceScope, DeviceScope.device_id == Device.id).join(
            Membership, Membership.group_id == DeviceScope.group_id).where(Membership.user_id == user.id)
    return query


def device_context(db, device, entitlement):
    """Return live authority, or None. Every credential endpoint must call this."""
    if not entitlement or entitlement.expires <= time.time():
        return None
    scope = db.get(DeviceScope, device.id)
    if scope is None:
        # Pre-group installations remain organisation-owned and admin-visible only.
        return dict(group_id=None, group_name='Organisation device', owner_email=None,
                    camera_limit=min(4, entitlement.camera_limit))
    group, policy = db.get(Group, scope.group_id), db.get(GroupLicence, scope.group_id)
    owner = db.get(User, scope.user_id)
    if (not group or group.organisation_id != device.organisation_id or not policy or not policy.enabled
            or not owner or not owner.verified or owner.organisation_id != device.organisation_id
            or owner.role not in {'admin', 'operator'}):
        return None
    if owner.role != 'admin' and not db.get(Membership, (owner.id, group.id)):
        return None
    return dict(group_id=group.id, group_name=group.name, owner_email=owner.email,
                camera_limit=min(4, policy.camera_limit, entitlement.camera_limit))


def revoke_user_devices(db, user_id, keep_groups=()):
    query = select(DeviceScope.device_id).where(DeviceScope.user_id == user_id)
    if keep_groups:
        query = query.where(DeviceScope.group_id.not_in(keep_groups))
    db.execute(update(Device).where(Device.id.in_(query), Device.status.in_(['pending', 'active']))
        .values(status='revoked', activation_hash=None, credential_hash=None))
