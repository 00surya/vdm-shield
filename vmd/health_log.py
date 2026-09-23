"""Bounded operational health history; no camera URLs are logged."""
import sqlite3
import threading
import time
import shutil

class HealthLog:
    def __init__(self, directory):
        self.path = directory / 'health.sqlite3'
        self.lock = threading.Lock()
        self.previous = {}
        self.last_summary = 0
        with sqlite3.connect(self.path) as db:
            db.execute('CREATE TABLE IF NOT EXISTS logs (id INTEGER PRIMARY KEY, created REAL, level TEXT, message TEXT)')
        self.append('info', 'Monitoring service started.')

    def append(self, level, message):
        with sqlite3.connect(self.path) as db:
            db.execute('INSERT INTO logs(created,level,message) VALUES(?,?,?)', (time.time(), level, message))
            db.execute('DELETE FROM logs WHERE id NOT IN (SELECT id FROM logs ORDER BY id DESC LIMIT 2000)')

    def observe(self, sources, evidence):
        with self.lock:
            cameras = [s for s in sources if s.get('source_kind') != 'upload']
            online = sum(s.get('status') == 'running' and not s.get('stale') for s in cameras)
            current = {}
            for s in cameras:
                key = s.get('camera_id', 'unknown')
                status = s.get('status', 'unknown')
                faults = []
                if s.get('stale'): faults.append('video stale')
                if status == 'running':
                    target = min(s.get('capture_fps') or s.get('target_fps', 20), s.get('target_fps', 20))
                    if s.get('processed_frames', 0) >= 30 and s.get('processed_fps', 0) < target * .9: faults.append('pose/motion below target')
                    if s.get('depth_status') != 'ready': faults.append('depth not ready')
                    if s.get('threat_status') != 'ready' or s.get('threat_stale'): faults.append('object/weapon checks unavailable or delayed')
                message = status + ('; ' + ', '.join(faults) if faults else '')
                current[key] = message
                if self.previous.get(key) != message:
                    self.append('warning' if faults or status == 'error' else 'info', 'Camera ' + key + ': ' + message)
            for key in self.previous.keys() - current.keys():
                if not key.startswith('_'): self.append('info', 'Camera ' + key + ': removed')
            current['_writer'] = 'Evidence writer needs attention' if evidence['last_error'] or not evidence['writer_alive'] else 'Evidence writer ready'
            if self.previous.get('_writer') != current['_writer']:
                self.append('warning' if evidence['last_error'] or not evidence['writer_alive'] else 'info', current['_writer'])
            disk = shutil.disk_usage(self.path.parent)
            current['_disk'] = 'Low storage' if disk.free < max(5*1024**3, disk.total*.1) else 'Storage reserve available'
            if self.previous.get('_disk') != current['_disk']:
                self.append('warning' if current['_disk'] == 'Low storage' else 'info', current['_disk'])
            if time.monotonic() - self.last_summary >= 60:
                self.append('info', f"Health: {online}/{len(cameras)} cameras online; {len(cameras)-online} not online; {evidence['queued_jobs']} evidence jobs queued; {evidence['dropped_records']} dropped records.")
                self.last_summary = time.monotonic()
            self.previous = current
            return {'online': online, 'configured': len(cameras), 'not_online': len(cameras)-online}

    def read(self, level):
        with self.lock, sqlite3.connect(self.path) as db:
            db.row_factory = sqlite3.Row
            return [dict(r) for r in db.execute("SELECT * FROM logs WHERE (?='all' OR level=?) ORDER BY id DESC LIMIT 200", (level, level))]
