"""Explicit numeric-only cloud snapshot. Never serialize engine/store records."""
import math
import shutil
import time

FIELDS = {'schema', 'source_count', 'running_sources', 'stale_sources', 'analysis_fps',
          'free_disk_gb', 'alerts_24h', 'confirmed_24h', 'false_positive_24h', 'unreviewed_24h'}
INTEGER_FIELDS = FIELDS - {'analysis_fps', 'free_disk_gb'}


def validate_snapshot(value):
    if not isinstance(value, dict) or set(value) != FIELDS:
        raise ValueError('Only the documented numeric snapshot fields are accepted.')
    for key, number in value.items():
        if isinstance(number, bool) or not isinstance(number, (int, float)) or not 0 <= number <= 1_000_000_000 or not math.isfinite(number):
            raise ValueError('Snapshot values must be finite non-negative numbers.')
        if key in INTEGER_FIELDS and not isinstance(number, int):
            raise ValueError('Snapshot counters must be integers.')
    if value['schema'] != 1 or value['source_count'] > 4 or value['running_sources'] > value['source_count'] or value['stale_sources'] > value['running_sources']:
        raise ValueError('Invalid snapshot version or source counts.')
    if sum(value[key] for key in ('confirmed_24h', 'false_positive_24h', 'unreviewed_24h')) != value['alerts_24h']:
        raise ValueError('Review counters must sum to total alerts.')
    return dict(value)


def collect_snapshot(manager):
    sources = manager.list()
    active = [source for source in sources if source.get('status') == 'running']
    with manager.store.connect() as db:
        rows = db.execute("SELECT review, COUNT(*) FROM incidents WHERE created >= ? AND mode = 'live' GROUP BY review", (time.time() - 86400,)).fetchall()
    counts = dict(rows)
    total = sum(counts.values())
    return validate_snapshot({
        'schema': 1, 'source_count': len(sources), 'running_sources': len(active),
        'stale_sources': sum(bool(source.get('stale')) for source in active),
        'analysis_fps': round(sum(max(0, float(source.get('processed_fps', source.get('fps', 0)) or 0)) for source in active), 2),
        'free_disk_gb': round(shutil.disk_usage(manager.data_dir).free / 1_000_000_000, 2),
        'alerts_24h': total, 'confirmed_24h': counts.get('confirmed', 0),
        'false_positive_24h': counts.get('false_positive', 0),
        'unreviewed_24h': total - counts.get('confirmed', 0) - counts.get('false_positive', 0),
    })
