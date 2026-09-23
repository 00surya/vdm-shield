from sqlalchemy import Boolean, ForeignKey, String, Integer
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Organisation(Base):
    __tablename__ = 'organisations'
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160))


class User(Base):
    __tablename__ = 'users'
    id: Mapped[int] = mapped_column(primary_key=True)
    organisation_id: Mapped[int] = mapped_column(ForeignKey('organisations.id'))
    email: Mapped[str] = mapped_column(String(254), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    role: Mapped[str] = mapped_column(String(20), default='admin')


class LoginSession(Base):
    __tablename__ = 'login_sessions'
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'))
    expires: Mapped[int] = mapped_column(Integer)


class RateLimit(Base):
    __tablename__ = 'rate_limits'
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    count: Mapped[int] = mapped_column(Integer)
    expires: Mapped[int] = mapped_column(Integer)


class Group(Base):
    __tablename__ = 'groups'
    id: Mapped[int] = mapped_column(primary_key=True)
    organisation_id: Mapped[int] = mapped_column(ForeignKey('organisations.id'))
    name: Mapped[str] = mapped_column(String(100))


class Membership(Base):
    __tablename__ = 'memberships'
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey('groups.id'), primary_key=True)


class Invitation(Base):
    __tablename__ = 'invitations'
    id: Mapped[int] = mapped_column(primary_key=True)
    organisation_id: Mapped[int] = mapped_column(ForeignKey('organisations.id'))
    group_id: Mapped[int] = mapped_column(ForeignKey('groups.id'))
    email: Mapped[str] = mapped_column(String(254))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default='pending')


class Entitlement(Base):
    __tablename__ = 'entitlements'
    organisation_id: Mapped[int] = mapped_column(ForeignKey('organisations.id'), primary_key=True)
    expires: Mapped[int] = mapped_column(Integer)
    device_limit: Mapped[int] = mapped_column(Integer)
    camera_limit: Mapped[int] = mapped_column(Integer)


class Device(Base):
    __tablename__ = 'devices'
    id: Mapped[int] = mapped_column(primary_key=True)
    organisation_id: Mapped[int] = mapped_column(ForeignKey('organisations.id'))
    name: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(20), default='pending')
    activation_hash: Mapped[str | None] = mapped_column(String(64), unique=True)
    activation_expires: Mapped[int] = mapped_column(Integer)
    credential_hash: Mapped[str | None] = mapped_column(String(64), unique=True)
    last_seen: Mapped[int] = mapped_column(Integer, default=0)


class RenewalReceipt(Base):
    __tablename__ = 'renewal_receipts'
    credential_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    device_id: Mapped[int] = mapped_column(ForeignKey('devices.id'))
    new_hash: Mapped[str] = mapped_column(String(64))
    expires: Mapped[int] = mapped_column(Integer)


class DeviceSnapshot(Base):
    __tablename__ = 'device_snapshots'
    device_id: Mapped[int] = mapped_column(ForeignKey('devices.id'), primary_key=True)
    received_at: Mapped[int] = mapped_column(Integer)
    payload: Mapped[str] = mapped_column(String(4096))


class GroupLicence(Base):
    __tablename__ = 'group_licences'
    group_id: Mapped[int] = mapped_column(ForeignKey('groups.id'), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    device_limit: Mapped[int] = mapped_column(Integer, default=1)
    camera_limit: Mapped[int] = mapped_column(Integer, default=4)


class DeviceScope(Base):
    """A device enrollment belongs to one group and the person who issued its code."""
    __tablename__ = 'device_scopes'
    device_id: Mapped[int] = mapped_column(ForeignKey('devices.id'), primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey('groups.id'))
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'))


class TrialGrant(Base):
    __tablename__ = 'trial_grants'
    organisation_id: Mapped[int] = mapped_column(ForeignKey('organisations.id'), primary_key=True)
    granted_at: Mapped[int] = mapped_column(Integer)


class AuditEvent(Base):
    __tablename__ = 'audit_events'
    id: Mapped[int] = mapped_column(primary_key=True)
    organisation_id: Mapped[int] = mapped_column(ForeignKey('organisations.id'), index=True)
    actor_email: Mapped[str] = mapped_column(String(254))
    action: Mapped[str] = mapped_column(String(80))
    detail: Mapped[str] = mapped_column(String(1000))
    created_at: Mapped[int] = mapped_column(Integer)


class PasswordReset(Base):
    __tablename__ = 'password_resets'
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'))
    expires: Mapped[int] = mapped_column(Integer)
    used: Mapped[bool] = mapped_column(Boolean, default=False)
