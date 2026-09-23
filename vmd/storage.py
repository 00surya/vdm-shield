"""SQLite writes and evidence encoding run on one bounded background queue."""
import json
import queue
import sqlite3
import threading
import time
import uuid
import hashlib
from collections import Counter, defaultdict, deque
from pathlib import Path

import cv2
import numpy as np


class EvidenceBuffer:
    def __init__(self, seconds=10, max_bytes=32 * 1024 * 1024):
        self.seconds, self.max_bytes = seconds, max_bytes
        self.frames = deque()
        self.bytes = 0

    def append(self, timestamp, jpeg):
        self.frames.append((timestamp, jpeg))
        self.bytes += len(jpeg)
        while self.frames and (timestamp - self.frames[0][0] > self.seconds or self.bytes > self.max_bytes):
            self.bytes -= len(self.frames.popleft()[1])

    def snapshot(self):
        return list(self.frames)


class Store:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.clips = self.directory / "clips"
        self.clips.mkdir(exist_ok=True)
        self.raw_clips = self.directory / "raw_clips"
        self.raw_clips.mkdir(exist_ok=True)
        self.db = self.directory / "telemetry.sqlite3"
        with self.connect() as conn:
            conn.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS training_uploads (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, label TEXT NOT NULL,
                    camera_id TEXT NOT NULL, raw_clip TEXT NOT NULL, created REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS uploads (filename TEXT PRIMARY KEY, name TEXT NOT NULL, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS incidents (
                    id TEXT PRIMARY KEY, created REAL NOT NULL, mode TEXT NOT NULL,
                    score REAL NOT NULL, reasons TEXT NOT NULL, signals TEXT NOT NULL,
                    review TEXT NOT NULL DEFAULT 'unreviewed', clip TEXT, clip_error TEXT
                );
                CREATE TABLE IF NOT EXISTS telemetry (
                    created REAL NOT NULL, people INTEGER NOT NULL, score REAL NOT NULL, mode TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS telemetry_time ON telemetry(created);
                CREATE INDEX IF NOT EXISTS incidents_time ON incidents(created);
                CREATE TABLE IF NOT EXISTS normal_samples (
                    id TEXT PRIMARY KEY, created REAL NOT NULL, camera_id TEXT NOT NULL,
                    raw_clip TEXT NOT NULL, review TEXT NOT NULL DEFAULT 'unreviewed'
                );
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            """)
            for table, columns in {
                "incidents": {"camera_id": "TEXT NOT NULL DEFAULT 'camera-1'", "camera_name": "TEXT NOT NULL DEFAULT 'Camera 1'", "event_type": "TEXT NOT NULL DEFAULT 'fight'",
                              "source_start": "REAL", "source_end": "REAL", "event_count": "INTEGER NOT NULL DEFAULT 1",
                              "raw_clip": "TEXT", "train_label": "TEXT",
                              "evidence_start": "REAL", "evidence_end": "REAL", "raw_start": "REAL", "raw_end": "REAL",
                              "evidence_expires_at": "REAL"},
                "telemetry": {"camera_id": "TEXT NOT NULL DEFAULT 'camera-1'"},
                "normal_samples": {"expires_at": "REAL"},
            }.items():
                existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
                for column, declaration in columns.items():
                    if column not in existing:
                        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")
            row = conn.execute("SELECT value FROM settings WHERE key='evidence_retention_days'").fetchone()
            self.evidence_retention_days = int(row[0]) if row else 7
        self._next_cleanup_at = 0
        self.expire_evidence()
        self.jobs = queue.Queue(maxsize=32)
        self.error = None
        self.last_evidence_saved_at = None
        self.last_evidence_error = None
        self.dropped = 0
        self.worker = threading.Thread(target=self._work, daemon=True, name="evidence-writer")
        self.worker.start()
        from .learning import Learner
        self.learner = Learner(self)

    def connect(self):
        return sqlite3.connect(self.db, timeout=10)

    def enqueue(self, kind, payload):
        try:
            self.jobs.put_nowait((kind, payload))
            return True
        except queue.Full:
            self.dropped += 1
            self.error = "Storage queue full; a record was not saved"
            return False

    def _work(self):
        while True:
            try:
                job = self.jobs.get(timeout=300)
            except queue.Empty:
                self._maybe_expire_evidence()
                continue
            try:
                if job is None:
                    return
                kind, payload = job
                if kind == "incident":
                    self._incident(*payload)
                elif kind == "incident_update":
                    self._incident_update(*payload)
                elif kind == "normal_sample":
                    self._normal_sample(*payload)
                else:
                    with self.connect() as conn:
                        conn.execute("INSERT INTO telemetry (created,people,score,mode,camera_id) VALUES (?, ?, ?, ?, ?)", payload if len(payload)==5 else (*payload, "camera-1"))
            except Exception as exc:
                self.error = f"Storage failed: {type(exc).__name__}"
            finally:
                self.jobs.task_done()
                self._maybe_expire_evidence()

    def _maybe_expire_evidence(self):
        if time.time() >= self._next_cleanup_at:
            try:
                self.expire_evidence()
            except (OSError, sqlite3.Error):
                self.error = "Evidence retention cleanup failed"

    def set_evidence_retention_days(self, days):
        if not isinstance(days, int) or isinstance(days, bool) or not 1 <= days <= 365:
            raise ValueError("Evidence retention must be 1–365 days.")
        with self.connect() as conn:
            conn.execute("INSERT OR REPLACE INTO settings (key,value) VALUES ('evidence_retention_days',?)", (str(days),))
        self.evidence_retention_days = days
        return days

    def expire_evidence(self, now=None):
        """Expire only new managed clips; legacy rows with NULL expiry are untouched."""
        now = time.time() if now is None else now
        from .controllers.events import encode_lock
        removed = 0
        with encode_lock, self.connect() as conn:
            conn.row_factory = sqlite3.Row
            incidents = conn.execute(
                "SELECT id,clip,raw_clip FROM incidents WHERE evidence_expires_at IS NOT NULL "
                "AND evidence_expires_at<=? AND (clip IS NOT NULL OR raw_clip IS NOT NULL)", (now,)).fetchall()
            for item in incidents:
                for root, name in ((self.clips, item['clip']), (self.raw_clips, item['raw_clip'])):
                    if name and Path(name).name == name:
                        path = root / name
                        if not path.is_symlink():
                            path.unlink(missing_ok=True)
                playback = self.directory / 'playback'
                if playback.is_dir():
                    prefix = hashlib.sha256(item['id'].encode()).hexdigest()
                    for cached in playback.glob(f'{prefix}-*.mp4'):
                        if not cached.is_symlink():
                            cached.unlink(missing_ok=True)
                conn.execute("UPDATE incidents SET clip=NULL,raw_clip=NULL,clip_error='Evidence expired after retention period' WHERE id=?", (item['id'],))
                removed += 1
            samples = conn.execute(
                "SELECT id,raw_clip FROM normal_samples WHERE expires_at IS NOT NULL AND expires_at<=?", (now,)).fetchall()
            for sample in samples:
                name = sample['raw_clip']
                if Path(name).name == name:
                    path = self.raw_clips / name
                    if not path.is_symlink():
                        path.unlink(missing_ok=True)
                conn.execute("DELETE FROM normal_samples WHERE id=?", (sample['id'],))
                removed += 1
        self._next_cleanup_at = time.time() + 300
        return removed

    def _encode_clip(self, event, frames, directory=None):
        directory = directory or self.clips
        clip, error = None, None
        if len(frames) >= 2:
            filename = event["id"] + ".avi"
            temporary = directory / (event["id"] + ".tmp.avi")
            first = cv2.imdecode(np.frombuffer(frames[0][1], np.uint8), cv2.IMREAD_COLOR)
            duration = frames[-1][0] - frames[0][0]
            writer = None
            try:
                if first is None or duration <= 0:
                    raise ValueError("No decodable evidence")
                # Resample against source timestamps, preserving gaps and clip duration.
                fps = 10
                writer = cv2.VideoWriter(str(temporary), cv2.VideoWriter_fourcc(*"MJPG"), fps, (first.shape[1], first.shape[0]))
                if not writer.isOpened():
                    raise RuntimeError("Video encoder unavailable")
                index = 0
                for offset in np.arange(0, duration + 0.001, 1 / fps):
                    while index + 1 < len(frames) and frames[index+1][0] <= frames[0][0] + offset:
                        index += 1
                    frame = cv2.imdecode(np.frombuffer(frames[index][1], np.uint8), cv2.IMREAD_COLOR)
                    if frame is None:
                        raise ValueError("Invalid buffered frame")
                    if frame.shape[:2] != first.shape[:2]:
                        frame = cv2.resize(frame, (first.shape[1], first.shape[0]))
                    writer.write(frame)
                writer.release()
                writer = None
                temporary.replace(directory / filename)
                clip = filename
            except Exception as exc:
                error = f"Clip could not be encoded: {type(exc).__name__}"
            finally:
                if writer is not None:
                    writer.release()
                temporary.unlink(missing_ok=True)
        else:
            error = "Not enough pre-event frames yet"
        return clip, error

    @staticmethod
    def _save_clip_times(conn, incident_id, clip, frames, raw_clip, raw_frames):
        if clip and frames:
            conn.execute("UPDATE incidents SET evidence_start=?,evidence_end=? WHERE id=?",
                         (frames[0][0], frames[-1][0], incident_id))
        if raw_clip and raw_frames:
            conn.execute("UPDATE incidents SET raw_start=?,raw_end=? WHERE id=?",
                         (raw_frames[0][0], raw_frames[-1][0], incident_id))

    def evidence_health(self):
        return {"last_saved_at": self.last_evidence_saved_at,
                "last_error": self.last_evidence_error or self.error,
                "queued_jobs": self.jobs.qsize(), "dropped_records": self.dropped,
                "writer_alive": self.worker.is_alive(),
                "scope": "since_service_start"}

    def _incident(self, event, frames, raw_frames=None):
        clip, error = self._encode_clip(event, frames)
        raw_clip, _ = self._encode_clip(event, raw_frames or [], self.raw_clips)
        source_time = event["signals"].get("source_seconds")
        with self.connect() as conn:
            conn.execute("INSERT INTO incidents (id,created,mode,score,reasons,signals,clip,clip_error,camera_id,camera_name,event_type,source_start,source_end,event_count,raw_clip,evidence_expires_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                event["id"], event["created"], event["mode"], event["score"], json.dumps(event["reasons"]), json.dumps(event["signals"]), clip, error, event.get("camera_id", "camera-1"), event.get("camera_name", "Camera 1"), event.get("event_type", "fight"), source_time, source_time, 1, raw_clip, event["created"] + self.evidence_retention_days * 86400))
            self._save_clip_times(conn, event["id"], clip, frames, raw_clip, raw_frames)
        if clip:
            self.last_evidence_saved_at = time.time()
            self.last_evidence_error = None
        else:
            self.last_evidence_error = error

    def _incident_update(self, event, frames, raw_frames=None):
        clip, error = self._encode_clip(event, frames)
        raw_clip, _ = self._encode_clip(event, raw_frames or [], self.raw_clips)
        with self.connect() as conn:
            conn.row_factory = sqlite3.Row
            old = conn.execute("SELECT reasons,clip,clip_error FROM incidents WHERE id=?", (event["id"],)).fetchone()
            if old is None:
                raise RuntimeError("Episode update arrived before its incident")
            reasons = list(dict.fromkeys([*json.loads(old["reasons"]), *event["reasons"]]))
            conn.execute("UPDATE incidents SET score=MAX(score,?), reasons=?, signals=?, clip=?, clip_error=?, source_end=?, event_count=?, raw_clip=COALESCE(?,raw_clip) WHERE id=?", (
                event["score"], json.dumps(reasons), json.dumps(event["signals"]),
                clip or old["clip"], error if clip is None else None,
                event["signals"].get("source_seconds"), event["event_count"], raw_clip, event["id"]))
            self._save_clip_times(conn, event["id"], clip, frames, raw_clip, raw_frames)
        if clip:
            self.last_evidence_saved_at = time.time()
            self.last_evidence_error = None
        else:
            self.last_evidence_error = error

    def _normal_sample(self, camera_id, created, frames):
        sample = {"id": uuid.uuid4().hex}
        raw_clip, _ = self._encode_clip(sample, frames, self.raw_clips)
        if raw_clip:
            with self.connect() as conn:
                conn.execute("INSERT INTO normal_samples (id,created,camera_id,raw_clip,expires_at) VALUES (?,?,?,?,?)",
                             (sample["id"], created, camera_id, raw_clip, created + self.evidence_retention_days * 86400))

    def incidents(self):
        with self.connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT * FROM incidents ORDER BY created DESC LIMIT 100").fetchall()
        return [{**dict(row), "reasons": json.loads(row["reasons"]), "signals": json.loads(row["signals"])} for row in rows]

    def review(self, incident_id, decision):
        with self.connect() as conn:
            return conn.execute("UPDATE incidents SET review=?, train_label=CASE WHEN ?='false_positive' THEN 'normal' WHEN ?='unreviewed' THEN NULL WHEN train_label='normal' THEN NULL ELSE train_label END WHERE id=?",
                                (decision, decision, decision, incident_id)).rowcount > 0

    def set_training_label(self, incident_id, label):
        with self.connect() as conn:
            return conn.execute("UPDATE incidents SET train_label=?,review=? WHERE id=?", (label, "false_positive" if label == "normal" else "confirmed", incident_id)).rowcount > 0

    def training_examples(self):
        with self.connect() as conn:
            incidents = conn.execute("SELECT event_type,review,train_label,raw_clip,signals,camera_id FROM incidents WHERE mode='live' AND raw_clip IS NOT NULL").fetchall()
            normal = conn.execute("SELECT raw_clip,review,camera_id FROM normal_samples WHERE review!='rejected'").fetchall()
        examples = [{"raw_clip": row[3], "label": row[2] or ("normal" if row[1] == "false_positive" else row[0]),
                     "weight": 3 if row[2] or row[1] in {"confirmed", "false_positive"} else 1,
                     "camera_id": row[5], "reviewed": bool(row[2]) or row[1] != "unreviewed"}
                    for row in incidents if not (json.loads(row[4]).get("model_generated") and row[1] == "unreviewed" and not row[2])]
        examples.extend({"raw_clip": row[0], "label": "normal", "weight": 3 if row[1] == "accepted" else 1,
                         "camera_id": row[2], "reviewed": row[1] == "accepted"} for row in normal)
        with self.connect() as conn:
            rows = conn.execute('SELECT raw_clip,label,camera_id FROM training_uploads').fetchall()
        examples.extend({'raw_clip': r[0], 'label': r[1], 'camera_id': r[2], 'weight': 3, 'reviewed': True} for r in rows)
        return examples

    def normal_samples(self):
        with self.connect() as conn:
            conn.row_factory = sqlite3.Row
            return [dict(row) for row in conn.execute("SELECT * FROM normal_samples ORDER BY created DESC LIMIT 20")]

    def review_normal(self, sample_id, decision):
        with self.connect() as conn:
            return conn.execute("UPDATE normal_samples SET review=? WHERE id=?", (decision, sample_id)).rowcount > 0

    def training_counts(self):
        counts = {}
        for item in self.training_examples():
            counts[item["label"]] = counts.get(item["label"], 0) + 1
        return counts

    def training_audit(self):
        """Summarize candidate clip labels without treating pseudo-labels as ground truth."""
        with self.connect() as conn:
            conn.row_factory = sqlite3.Row
            incidents = conn.execute(
                "SELECT camera_id,event_type,review,train_label,raw_clip,signals "
                "FROM incidents WHERE mode='live'"
            ).fetchall()
            normal_samples = conn.execute(
                "SELECT camera_id,review,raw_clip FROM normal_samples"
            ).fetchall()

        labels = defaultdict(lambda: {"count": 0, "reviewed": 0, "provisional": 0, "sources": set()})
        sources = Counter()
        reviewed_sources = set()
        origin = Counter()
        missing_raw = 0
        excluded_model = 0
        rejected_normal = 0

        def add(label, camera_id, reviewed, kind):
            entry = labels[label]
            entry["count"] += 1
            entry["reviewed" if reviewed else "provisional"] += 1
            entry["sources"].add(camera_id)
            sources[camera_id] += 1
            origin[kind] += 1
            if reviewed:
                reviewed_sources.add(camera_id)

        for row in incidents:
            signals = json.loads(row["signals"])
            if signals.get("model_generated") and row["review"] == "unreviewed" and not row["train_label"]:
                excluded_model += 1
                continue
            raw = row["raw_clip"]
            if not raw or not (self.raw_clips / raw).is_file():
                missing_raw += 1
                continue
            label = row["train_label"] or ("normal" if row["review"] == "false_positive" else row["event_type"])
            reviewed = row["review"] != "unreviewed" or bool(row["train_label"])
            add(label, row["camera_id"], reviewed, "operator_reviewed" if reviewed else "heuristic_proposed")

        for row in normal_samples:
            if row["review"] == "rejected":
                rejected_normal += 1
                continue
            if not (self.raw_clips / row["raw_clip"]).is_file():
                missing_raw += 1
                continue
            reviewed = row["review"] == "accepted"
            add("normal", row["camera_id"], reviewed, "operator_reviewed" if reviewed else "auto_normal")

        with self.connect() as conn:
            for raw_clip, label, camera_id in conn.execute('SELECT raw_clip,label,camera_id FROM training_uploads'):
                if (self.raw_clips / raw_clip).is_file():
                    add(label, camera_id, True, 'operator_reviewed')
                else:
                    missing_raw += 1
        total = sum(entry["count"] for entry in labels.values())
        reviewed = origin["operator_reviewed"]
        ordered = sorted(labels.items(), key=lambda item: (-item[1]["count"], item[0]))
        label_rows = [{"label": label, "count": entry["count"], "reviewed": entry["reviewed"],
                       "provisional": entry["provisional"], "sources": len(entry["sources"])}
                      for label, entry in ordered]
        dominant_label = ordered[0][0] if ordered else None
        dominant_share = round(ordered[0][1]["count"] / total, 3) if total else 0
        dominant_source_share = round(max(sources.values()) / total, 3) if total else 0
        normal_count = labels["normal"]["count"]
        event_counts = {label: entry["count"] for label, entry in labels.items() if label != "normal"}
        training_ready = normal_count >= 3 and any(count >= 2 for count in event_counts.values())
        reviewed_normal = labels["normal"]["reviewed"]
        reviewed_event = sum(entry["reviewed"] for label, entry in labels.items() if label != "normal")
        # Source-separated validation also checks the actual per-source label split at training time.
        validation_ready = reviewed >= 20 and reviewed_normal >= 5 and reviewed_event >= 5 and len(reviewed_sources) >= 3
        findings = []
        if not training_ready:
            findings.append({"level": "blocker", "title": "Too few clips to train",
                             "detail": "Training needs at least 3 normal clips and 2 clips of one event type."})
        if total and (total - reviewed) / total >= .5:
            findings.append({"level": "high", "title": "Most labels are provisional",
                             "detail": "Heuristic and automatic labels can repeat the detector's mistakes."})
        if total >= 2 and dominant_share >= .7:
            findings.append({"level": "high", "title": "One label dominates",
                             "detail": f"{dominant_label.replace('_', ' ')} is {round(dominant_share * 100)}% of candidate clips; rarer events may be missed."})
        if total >= 2 and dominant_source_share >= .7:
            findings.append({"level": "high", "title": "Footage comes mostly from one source",
                             "detail": f"{round(dominant_source_share * 100)}% of clips share one source; results may not transfer to other cameras or scenes."})
        if missing_raw:
            findings.append({"level": "medium", "title": "Some raw clips are missing",
                             "detail": f"{missing_raw} record(s) have no available raw clip and cannot train."})
        if not validation_ready:
            findings.append({"level": "medium", "title": "No reliable performance estimate yet",
                             "detail": "Collect at least 20 operator-reviewed clips, including 5 normal and 5 event clips across 3 sources, then validate on held-out sources."})
        findings.append({"level": "info", "title": "Missed incidents are not represented",
                         "detail": "Event clips are selected by the existing detector, so this data alone cannot measure recall on continuous footage."})
        return {
            "candidate_clips": total,
            "reviewed_clips": reviewed,
            "provisional_clips": total - reviewed,
            "source_count": len(sources),
            "dominant_label_share": dominant_share,
            "dominant_source_share": dominant_source_share,
            "training_ready": training_ready,
            "validation_ready": validation_ready,
            "missing_raw": missing_raw,
            "excluded_model_generated": excluded_model,
            "rejected_normal": rejected_normal,
            "origin": dict(origin),
            "labels": label_rows,
            "findings": findings,
        }

    def analytics(self, since):
        with self.connect() as conn:
            rows = conn.execute("SELECT CAST(created / 3600 AS INTEGER)*3600, AVG(people), MAX(people), COUNT(*), camera_id FROM telemetry WHERE created>=? AND mode='live' GROUP BY 1, camera_id ORDER BY 1, camera_id", (since,)).fetchall()
        return [{"hour": row[0], "average": round(row[1], 1), "peak": row[2], "samples": row[3], "camera_id": row[4]} for row in rows]

    def dashboard_metrics(self, since, bucket_seconds, heatmap_since=None, timezone_offset_minutes=0):
        shift = -timezone_offset_minutes * 60
        with self.connect() as conn:
            conn.row_factory = sqlite3.Row
            events = conn.execute(
                "SELECT CAST((created + ?) / ? AS INTEGER) * ? - ? AS bucket, COUNT(*) AS count "
                "FROM incidents WHERE created >= ? AND mode = 'live' GROUP BY bucket ORDER BY bucket",
                (shift, bucket_seconds, bucket_seconds, shift, since),
            ).fetchall()
            people = conn.execute(
                "SELECT CAST((created + ?) / ? AS INTEGER) * ? - ? AS bucket, "
                "COUNT(*) AS samples, ROUND(AVG(people), 1) AS average, MAX(people) AS peak "
                "FROM telemetry WHERE created >= ? AND mode = 'live' GROUP BY bucket ORDER BY bucket",
                (shift, bucket_seconds, bucket_seconds, shift, since),
            ).fetchall()
            reviews = conn.execute(
                "SELECT review, COUNT(*) AS count FROM incidents WHERE created >= ? "
                "AND mode = 'live' GROUP BY review", (since,),
            ).fetchall()
            types = conn.execute(
                "SELECT event_type, COUNT(*) AS count FROM incidents WHERE created >= ? "
                "AND mode = 'live' GROUP BY event_type ORDER BY count DESC, event_type", (since,),
            ).fetchall()
            heatmap_events = conn.execute(
                "SELECT CAST((created + ?) / 3600 AS INTEGER) * 3600 - ? AS bucket, COUNT(*) AS count "
                "FROM incidents WHERE created >= ? AND mode = 'live' GROUP BY bucket ORDER BY bucket",
                (shift, shift, heatmap_since if heatmap_since is not None else since),
            ).fetchall()
            heatmap_samples = conn.execute(
                "SELECT CAST((created + ?) / 3600 AS INTEGER) * 3600 - ? AS bucket, COUNT(*) AS samples "
                "FROM telemetry WHERE created >= ? AND mode = 'live' GROUP BY bucket ORDER BY bucket",
                (shift, shift, heatmap_since if heatmap_since is not None else since),
            ).fetchall()
        return {
            "events": [dict(row) for row in events],
            "people": [dict(row) for row in people],
            "reviews": {row["review"]: row["count"] for row in reviews},
            "types": [dict(row) for row in types],
            "heatmap_events": [dict(row) for row in heatmap_events],
            "heatmap_samples": [dict(row) for row in heatmap_samples],
        }

    def close(self):
        self.learner.close()
        self.jobs.put(None)
        self.worker.join(timeout=20)
