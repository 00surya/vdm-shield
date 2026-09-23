"""Authenticated, bounded latest-snapshot ingestion; administrator-only viewing."""
import json
import time
from flask import abort, jsonify, render_template, request
from sqlalchemy import select, update
from sqlalchemy.orm import Session
from .models import Device, Entitlement, DeviceSnapshot
from .licensing import digest
from .access import visible_devices, device_context
from vmd.metadata import validate_snapshot


def register_insights(app, engine, current_user):
    @app.post('/api/device/metadata')
    def ingest_metadata():
        if request.headers.get('Origin') or not request.is_json:
            return jsonify(error='Use JSON without an Origin header.'), 400
        auth = request.headers.get('Authorization', '')
        if not auth.startswith('Bearer ') or not 20 <= len(auth) <= 200:
            return jsonify(error='Device credential required.'), 401
        try:
            payload = validate_snapshot(request.get_json(silent=True))
        except ValueError as exc:
            return jsonify(error=str(exc)), 400
        now = int(time.time())
        with Session(engine) as db:
            # Serialize ingestion with renewal/revocation and derive ownership from credential.
            locked = db.execute(update(Device).where(Device.credential_hash == digest(auth[7:]), Device.status == 'active').values(last_seen=Device.last_seen))
            if locked.rowcount != 1:
                return jsonify(error='Device credential invalid or revoked.'), 401
            device = db.scalar(select(Device).where(Device.credential_hash == digest(auth[7:])))
            entitlement = db.get(Entitlement, device.organisation_id)
            if not entitlement or entitlement.expires <= now:
                return jsonify(error='Licence expired.'), 403
            if device_context(db, device, entitlement) is None:
                return jsonify(error='Group licence or member access unavailable.'), 403
            snapshot = db.get(DeviceSnapshot, device.id)
            if snapshot and now - snapshot.received_at < 15:
                return jsonify(error='Wait before sending another snapshot.'), 429
            if not snapshot:
                snapshot = DeviceSnapshot(device_id=device.id)
                db.add(snapshot)
            snapshot.received_at = now
            snapshot.payload = json.dumps(payload, allow_nan=False)
            db.commit()
        return jsonify(saved=True, received_at=now)

    @app.get('/insights')
    def insights():
        with Session(engine) as db:
            user = current_user(db)
            if not user:
                abort(403)
            rows = db.execute(select(Device, DeviceSnapshot).outerjoin(DeviceSnapshot, DeviceSnapshot.device_id == Device.id)
                              .where(Device.id.in_(visible_devices(user).with_only_columns(Device.id))).order_by(Device.id)).all()
            now = int(time.time())
            devices = [{'device': device, 'data': json.loads(snapshot.payload) if snapshot else None,
                        'received': snapshot.received_at if snapshot else None,
                        'fresh': bool(snapshot and now - snapshot.received_at <= 180 and device.status == 'active')}
                       for device, snapshot in rows]
            return render_template('insights.html', devices=devices)
