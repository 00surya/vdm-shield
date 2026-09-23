"""Time-separated event review and browser-compatible evidence playback."""
import hashlib
import json
import math
import sqlite3
import subprocess
import threading
import time
import uuid

from flask import Blueprint, jsonify, request, send_file
from .web import manager, error
from .library import evidence_item, evidence_path

events = Blueprint('events', __name__)
encode_lock = threading.Lock()


@events.get('/api/event-records')
def records():
    scope = request.args.get('scope', 'recent')
    if scope not in {'recent', 'history'}:
        return error('Choose recent events or history.')
    now = time.time()
    clauses = ["mode='live'", 'created >= ?' if scope == 'recent' else 'created < ?']
    args = [now - 12 * 3600]
    try:
        page = int(request.args.get('page', '1'))
        if page < 1:
            raise ValueError
        for name, op in [('from', '>='), ('to', '<=')]:
            if request.args.get(name):
                value = float(request.args[name])
                if not math.isfinite(value):
                    raise ValueError
                clauses.append(f'created {op} ?')
                args.append(value)
        if request.args.get('from') and request.args.get('to') and float(request.args['from']) > float(request.args['to']):
            raise ValueError
    except ValueError:
        return error('Choose a valid time range and page.')
    review = request.args.get('review', 'all')
    if review not in {'all', 'confirmed', 'unreviewed', 'false_positive'}:
        return error('Invalid review filter.')
    if review != 'all':
        clauses.append('review=?'); args.append(review)
    query = request.args.get('search', '').strip().lower()
    if query:
        clauses.append("instr(lower(replace(event_type,'_',' ') || ' ' || camera_name), ?) > 0")
        args.append(query)
    priority = request.args.get('priority', 'all')
    if priority not in {'all', 'high'}:
        return error('Invalid priority filter.')
    if priority == 'high':
        clauses.append("json_extract(signals,'$.priority')='high'")
    where = ' AND '.join(clauses)
    with manager().store.connect() as conn:
        conn.row_factory = sqlite3.Row
        total = conn.execute('SELECT COUNT(*) FROM incidents WHERE ' + where, args).fetchone()[0]
        rows = conn.execute('SELECT * FROM incidents WHERE ' + where + ' ORDER BY created DESC,id LIMIT 30 OFFSET ?', (*args, (page - 1) * 30)).fetchall()
    items = []
    for row in rows:
        item = dict(row)
        item['reasons'], item['signals'] = json.loads(item['reasons']), json.loads(item['signals'])
        item['clip'] = item['clip'] if evidence_path(item, 'annotated') else None
        item['raw_clip'] = item['raw_clip'] if evidence_path(item, 'raw') else None
        items.append(item)
    return jsonify(items=items, total=total, page=page, pages=max(1, math.ceil(total / 30)), cutoff=now - 12 * 3600)


@events.get('/api/library/evidence/<item_id>/play')
def play(item_id):
    item = evidence_item(item_id)
    if not item:
        return error('Event not found.', 404)
    source = evidence_path(item, 'annotated') or evidence_path(item, 'raw')
    if not source:
        return error('Evidence is missing or still being saved.', 404)
    stat = source.stat()
    token = hashlib.sha256(f'{source.name}:{stat.st_size}:{stat.st_mtime_ns}'.encode()).hexdigest()
    cache = manager().data_dir / 'playback'
    cache.mkdir(exist_ok=True)
    prefix = hashlib.sha256(item_id.encode()).hexdigest()
    target = cache / f'{prefix}-{token}.mp4'
    with encode_lock:
        if not source.is_file():
            return error('Evidence was removed.', 404)
        if not target.is_file():
            temporary = cache / f'{uuid.uuid4().hex}.mp4'
            try:
                import imageio_ffmpeg
                subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-nostdin', '-y', '-i', str(source),
                                '-an', '-vf', 'scale=trunc(iw/2)*2:trunc(ih/2)*2', '-c:v', 'libx264',
                                '-preset', 'ultrafast', '-crf', '23', '-pix_fmt', 'yuv420p', '-movflags', '+faststart',
                                str(temporary)], check=True, capture_output=True, timeout=60)
                temporary.replace(target)
            except (ImportError, OSError, subprocess.SubprocessError):
                return error('Playback conversion failed. Download the original clip instead.', 503)
            finally:
                temporary.unlink(missing_ok=True)
    return send_file(target, mimetype='video/mp4', conditional=True)
