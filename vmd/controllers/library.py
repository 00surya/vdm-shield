"""Operator-visible uploaded videos and saved event evidence."""
import io
import hashlib
import sqlite3
import time
import uuid
from pathlib import Path

import cv2
from flask import Blueprint, jsonify, request, send_file

from .web import manager, error, VIDEO_EXTENSIONS
from ..models.sources import SourceSettings

library = Blueprint('library', __name__)


def remember_upload(path, name):
    with manager().store.connect() as conn:
        conn.execute('CREATE TABLE IF NOT EXISTS uploads (filename TEXT PRIMARY KEY, name TEXT NOT NULL, created REAL NOT NULL)')
        conn.execute('INSERT OR REPLACE INTO uploads VALUES (?,?,?)', (path.name, name, time.time()))


def upload_path(filename):
    path = manager().uploads / filename
    if Path(filename).name != filename or path.suffix.lower() not in VIDEO_EXTENSIONS or path.is_symlink():
        return None
    return path if path.is_file() else None


def evidence_item(item_id):
    with manager().store.connect() as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM incidents WHERE id=? AND mode='live'", (item_id,)).fetchone()
    return dict(row) if row else None


def evidence_path(item, kind):
    name = item.get('raw_clip' if kind == 'raw' else 'clip')
    root = manager().store.raw_clips if kind == 'raw' else manager().store.clips
    if not name or Path(name).name != name:
        return None
    path = root / name
    return path if path.is_file() and not path.is_symlink() else None


@library.get('/api/library')
def inventory():
    with manager().store.connect() as conn:
        conn.execute('CREATE TABLE IF NOT EXISTS uploads (filename TEXT PRIMARY KEY, name TEXT NOT NULL, created REAL NOT NULL)')
        names = {row[0]: row[1:] for row in conn.execute('SELECT filename,name,created FROM uploads')}
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT id,created,camera_name,camera_id,event_type,review,clip,raw_clip FROM incidents WHERE mode='live' ORDER BY created DESC").fetchall()
    videos = []
    for path in manager().uploads.iterdir():
        if upload_path(path.name) is None:
            continue
        name, created = names.get(path.name, (f'Earlier upload · {path.name[:8]}{path.suffix}', path.stat().st_mtime))
        videos.append(dict(id=path.name, name=name, created=created, bytes=path.stat().st_size))
    evidence = []
    for row in rows:
        item = dict(row)
        item['annotated_available'] = evidence_path(item, 'annotated') is not None
        item['raw_available'] = evidence_path(item, 'raw') is not None
        evidence.append(item)
    return jsonify(videos=sorted(videos, key=lambda x: x['created'], reverse=True), evidence=evidence)


@library.post('/api/library/videos')
def upload():
    file = request.files.get('video')
    if not file or not file.filename or Path(file.filename).suffix.lower() not in VIDEO_EXTENSIONS:
        return error('Choose an MP4, MOV, AVI, MKV, WebM or M4V video.')
    path = manager().uploads / (uuid.uuid4().hex + Path(file.filename).suffix.lower())
    try:
        file.save(path)
        name = Path(file.filename).name[:200]
        remember_upload(path, name)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return jsonify(id=path.name, name=name), 201


@library.route('/api/library/videos/<filename>', methods=['GET', 'DELETE'])
def video_file(filename):
    path = upload_path(filename)
    if path is None:
        return error('Video not found.', 404)
    if request.method == 'GET':
        return send_file(path, as_attachment=True)
    # Remove retained source cards first, so Restart cannot reference a deleted video.
    with manager().lock:
        linked = [engine for engine in manager().cameras.values() if engine.settings.source == str(path)]
        if linked:
            return error('Remove this video from Sources before deleting its saved file. Removing a source keeps its evidence.', 409)
        path.unlink()
        with manager().store.connect() as conn:
            conn.execute('DELETE FROM uploads WHERE filename=?', (filename,))
    return jsonify(deleted=filename)


@library.post('/api/library/videos/<filename>/analyze')
def analyze_saved(filename):
    path = upload_path(filename)
    if path is None:
        return error('Video not found.', 404)
    with manager().store.connect() as conn:
        row = conn.execute('SELECT name FROM uploads WHERE filename=?', (filename,)).fetchone()
    try:
        return jsonify(manager().add(SourceSettings(source=str(path), name=row[0] if row else 'Saved video', kind='upload'))), 201
    except (ValueError, RuntimeError) as exc:
        return error(str(exc), 409)


@library.route('/api/library/evidence/<item_id>', methods=['GET', 'DELETE'])
def evidence_file(item_id):
    item = evidence_item(item_id)
    if not item:
        return error('Event not found.', 404)
    if request.method == 'DELETE':
        if manager().store.learner.snapshot()['state'] == 'training':
            return error('Wait for training to finish before deleting evidence.', 409)
        if manager().get(item['camera_id']) is not None or manager().store.jobs.unfinished_tasks:
            return error('Remove the related source and wait for evidence saving to finish before deleting.', 409)
        from .events import encode_lock
        with encode_lock:
            prefix = hashlib.sha256(item_id.encode()).hexdigest()
            for cached in (manager().data_dir / 'playback').glob(f'{prefix}-*.mp4'):
                cached.unlink(missing_ok=True)
            for kind in ('raw', 'annotated'):
                path = evidence_path(item, kind)
                if path:
                    path.unlink()
            with manager().store.connect() as conn:
                conn.execute('UPDATE incidents SET clip=NULL,raw_clip=NULL,clip_error=? WHERE id=?', ('Evidence deleted by operator', item_id))
        return jsonify(deleted=item_id)
    kind = request.args.get('kind', 'annotated')
    if kind not in {'raw', 'annotated'}:
        return error('Choose raw or annotated evidence.')
    path = evidence_path(item, kind)
    if path is None:
        return error('This evidence file is unavailable.', 404)
    return send_file(path, as_attachment=True)


@library.get('/api/library/evidence/<item_id>/preview')
def preview(item_id):
    item = evidence_item(item_id)
    if not item:
        return error('Event not found.', 404)
    path = evidence_path(item, 'annotated') or evidence_path(item, 'raw')
    if path is None:
        return error('No saved evidence to preview.', 404)
    return frame_response(path)


@library.get('/api/library/videos/<filename>/preview')
def video_preview(filename):
    path = upload_path(filename)
    if path is None:
        return error('Video not found.', 404)
    return frame_response(path)


def frame_response(path):
    """Decode a selected recorded frame; never requires browser video codecs."""
    cap = cv2.VideoCapture(str(path))
    try:
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        fps = float(cap.get(cv2.CAP_PROP_FPS))
        if total < 1:
            return error('This recording cannot be decoded.', 422)
        if request.args.get('metadata') == '1':
            return jsonify(frames=total, fps=fps if 0 < fps < 1000 else None,
                           duration_seconds=total / fps if 0 < fps < 1000 else None)
        try:
            if 'frame' in request.args:
                index = int(request.args['frame'])
                if not 0 <= index < total:
                    raise ValueError
            else:
                position = int(request.args.get('position', '50'))
                if not 0 <= position <= 100:
                    raise ValueError
                index = round((total - 1) * position / 100)
        except ValueError:
            return error('Choose a valid recorded frame or a position from 0–100.')
        cap.set(cv2.CAP_PROP_POS_FRAMES, index)
        ok, frame = cap.read()
        if not ok:
            return error('This frame cannot be decoded.', 422)
        height, width = frame.shape[:2]
        if width > 960:
            frame = cv2.resize(frame, (960, round(height * 960 / width)))
        ok, jpeg = cv2.imencode('.jpg', frame)
        if not ok:
            return error('Preview could not be generated.', 422)
        return send_file(io.BytesIO(jpeg.tobytes()), mimetype='image/jpeg',
                         as_attachment=request.args.get('download') == '1',
                         download_name=f'{path.stem}-frame-{index + 1}.jpg')
    finally:
        cap.release()
