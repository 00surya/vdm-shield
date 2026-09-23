"""Merge pinned public RGB weapon/ordnance annotations into a five-class candidate dataset."""
import argparse
from collections import Counter
import csv
import hashlib
import http.client
from pathlib import Path, PurePosixPath
import shutil
import sys
import time
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.prepare_weapon_data import download
from scripts.train_weapons import FIELDS, SPLITS, artifact_digest, audit, save_json

SOURCE = 'https://huggingface.co/datasets/UXO-Politehnica-Bucharest/Contextual_Vision_for_Unexploded_Ordnances'
REVISION = 'c1e7020b3a0192ab98861f1f07f0119a8b59c8d4'
SHA256 = '5e7f19066257513ac76d21e12202403f85e1a54eb8774d1c02998815910406e6'
SIZE = 3895410390
NAMES = ['gun', 'knife', 'grenade', 'bomb', 'other_ordnance']
# These are visual categories, not assertions about explosive content or whether an object is live.
MAPPING = {
    'AntiSubmarine Bomb': 'bomb', 'Aviation Bomb': 'bomb', 'Mortar Bomb': 'bomb',
    'Grenade': 'grenade', 'Cartridge': 'other_ordnance', 'Cartridge Magazine': 'other_ordnance',
    'Fuse': 'other_ordnance', 'LandMine': 'other_ordnance', 'Projectile': 'other_ordnance',
    'RPG': 'other_ordnance', 'Rocket': 'other_ordnance', 'Sea Mine': 'other_ordnance',
}


def download_range(url, target, start, end, total_size, retries=8):
    """Resume a part after short HTTP responses, without discarding bytes already received."""
    target = Path(target)
    expected = end - start + 1
    if target.exists() and target.stat().st_size == expected:
        return expected
    partial = target.with_suffix('.tmp')
    failures = 0
    while True:
        offset = partial.stat().st_size if partial.exists() else 0
        if offset > expected:
            raise ValueError('Partial byte range is larger than expected')
        if offset == expected:
            partial.replace(target)
            return expected
        # Small requests reduce the impact of interrupted long CDN connections.
        first, last = start + offset, min(start + offset + 8*1024*1024 - 1, end)
        request = urllib.request.Request(
            f'{url}&range_start={first}&range_end={last}&attempt={failures}',
            headers={'Range': f'bytes={first}-{last}'})
        try:
            with urllib.request.urlopen(request, timeout=60) as source:
                if source.status != 206 or source.headers.get('Content-Range') != f'bytes {first}-{last}/{total_size}':
                    raise ValueError('Server did not honor the requested byte range')
                received = 0
                with partial.open('ab') as out:
                    while received < last-first+1:
                        chunk = source.read(min(1024*1024, last-first+1-received))
                        if not chunk:
                            raise OSError('Incomplete byte range; saved bytes will be resumed')
                        out.write(chunk)
                        received += len(chunk)
            failures = 0
        except (OSError, http.client.HTTPException):
            current = partial.stat().st_size if partial.exists() else 0
            failures = 0 if current > offset else failures + 1
            if failures >= retries:
                raise
            time.sleep(min(failures + 1, 10))


def download_archive(cache, workers=8):
    """Download bounded ranges with resumable parts, then verify the pinned full archive."""
    cache = Path(cache)
    destination = cache / 'CTX-UXO.zip'
    if destination.exists():
        if artifact_digest(destination) != SHA256:
            raise ValueError('Cached CTX-UXO checksum mismatch')
        return destination
    parts = cache / 'CTX-UXO.parts'
    parts.mkdir(parents=True, exist_ok=True)
    temporary = cache / 'CTX-UXO.zip.part'
    size = 32 * 1024 * 1024
    ranges = [(i, start, min(start+size, SIZE)-1) for i, start in enumerate(range(0, SIZE, size))]
    # Reuse complete chunks from a previous sequential transfer, if present.
    if temporary.exists():
        with temporary.open('rb') as stream:
            for i, start, end in ranges:
                if end >= temporary.stat().st_size:
                    break
                target = parts / f'{i:04d}'
                chunk = stream.read(end-start+1)
                if not target.exists():
                    target.write_bytes(chunk)

    def fetch(item):
        i, start, end = item
        target = parts / f'{i:04d}'
        url = f'{SOURCE}/resolve/{REVISION}/CTX-UXO.zip?download=true&segment={i}'
        return download_range(url, target, start, end, SIZE)
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for result in as_completed([pool.submit(fetch, item) for item in ranges]):
            done += result.result()
            print(f'Dataset download: {done/1e9:.2f}/{SIZE/1e9:.2f} GB', flush=True)
    with temporary.open('wb') as out:
        for i, _, _ in ranges:
            with (parts / f'{i:04d}').open('rb') as stream:
                shutil.copyfileobj(stream, out)
    if artifact_digest(temporary) != SHA256:
        raise ValueError('Downloaded CTX-UXO checksum mismatch; partial files preserved for inspection')
    temporary.replace(destination)
    for i, _, _ in ranges:
        (parts / f'{i:04d}').unlink()
    parts.rmdir()
    return destination


def remap_labels(text, names):
    """Preserve every upstream box; reject malformed annotations instead of inventing negatives."""
    import math
    rows = []
    for line in text.splitlines():
        if not line.strip():
            continue
        parts = line.split()
        if len(parts) != 5 or not parts[0].isdigit() or int(parts[0]) >= len(names):
            raise ValueError('Invalid upstream annotation')
        x, y, w, h = map(float, parts[1:])
        if (not all(math.isfinite(v) for v in (x, y, w, h)) or not 0 < w <= 1 or not 0 < h <= 1
                or x-w/2 < -1e-5 or x+w/2 > 1+1e-5 or y-h/2 < -1e-5 or y+h/2 > 1+1e-5):
            raise ValueError('Invalid upstream bounding box')
        cls = NAMES.index(MAPPING[names[int(parts[0])]])
        rows.append(f'{cls} ' + ' '.join(parts[1:]))
    return '\n'.join(rows) + ('\n' if rows else '')


def prepare(starter, archive_path, output):
    import cv2
    import numpy as np
    import yaml
    starter, archive_path, output = map(lambda p: Path(p).resolve(), (starter, archive_path, output))
    if artifact_digest(archive_path) != SHA256:
        raise ValueError('CTX-UXO archive checksum mismatch')
    starter_report = audit(starter / 'dataset.yaml', imgsz=512, allow_upstream=True)
    if not starter_report['valid'] or starter_report['names'] != NAMES[:3]:
        raise ValueError('Expected the audited three-class public weapon starter')
    with zipfile.ZipFile(archive_path) as archive:
        infos = archive.infolist()
        if len(infos) > 20000 or sum(i.file_size for i in infos) > 8_000_000_000:
            raise ValueError('Unexpected archive size')
        members = set()
        for info in infos:
            path = PurePosixPath(info.filename)
            if (path.is_absolute() or '..' in path.parts or '\\' in info.filename or ':' in info.filename
                    or info.filename in members or info.file_size > 100_000_000):
                raise ValueError('Unsafe or unexpected archive entry')
            members.add(info.filename)
        config = yaml.safe_load(archive.read('yolo_bbox/data.yaml'))
        source_names = config['names']
        if set(source_names) != set(MAPPING) or len(source_names) != len(MAPPING):
            raise ValueError('Upstream class mapping changed')
        output.mkdir(parents=True, exist_ok=False)
        for split in SPLITS:
            for kind in ('images', 'labels'):
                (output / kind / split).mkdir(parents=True)
        rows, seen, stats, excluded = [], {}, Counter(), []
        with (starter / 'sources.csv').open(newline='') as stream:
            provenance = {row['image']: row for row in csv.DictReader(stream)}

        def add(split, filename, frame, labels, origin, group, existing=None):
            digest = hashlib.sha256(str(frame.shape).encode() + frame.tobytes()).hexdigest()
            if digest in seen:
                stats['duplicate_images_excluded'] += 1
                excluded.append(dict(image=filename, reason='identical decoded pixels', retained=seen[digest]))
                return
            seen[digest] = f'{split}/{filename}'
            target = output / 'images' / split / filename
            if existing:
                shutil.copy2(existing, target)
            elif not cv2.imwrite(str(target), frame, [cv2.IMWRITE_JPEG_QUALITY, 95]):
                raise ValueError('Could not write image')
            (output / 'labels' / split / Path(filename).with_suffix('.txt')).write_text(labels)
            rows.append(dict(image=target.relative_to(output).as_posix(), source_group=group,
                             origin=origin, usage_rights='CC BY 4.0; attribution in ATTRIBUTION.md', reviewed='upstream'))
            stats[f'{split}_images'] += 1

        # Preserve published partitions; when exact duplicates exist, held-out data wins.
        for split in ('test', 'val', 'train'):
            for image in sorted((starter / 'images' / split).glob('*')):
                record = provenance[image.relative_to(starter).as_posix()]
                frame = cv2.imread(str(image))
                label = (starter / 'labels' / split / image.name).with_suffix('.txt').read_text()
                add(split, 'weapon_' + image.name, frame, label, record['origin'], 'weapon:' + record['source_group'], image)
            upstream_split = 'valid' if split == 'val' else split
            prefix = f'images/{upstream_split}/images/'
            for name in sorted(n for n in members if n.startswith(prefix) and n.lower().endswith('.jpg')):
                stem = PurePosixPath(name).stem
                label_name = f'yolo_bbox/{upstream_split}/labels/{stem}.txt'
                if label_name not in members:
                    raise ValueError(f'Missing annotation: {label_name}')
                try:
                    labels = remap_labels(archive.read(label_name).decode(), source_names)
                except ValueError as error:
                    excluded.append(dict(image=name, reason=str(error)))
                    stats['invalid_annotations_excluded'] += 1
                    continue
                frame = cv2.imdecode(np.frombuffer(archive.read(name), dtype=np.uint8), cv2.IMREAD_COLOR)
                if frame is None:
                    raise ValueError(f'Image cannot be decoded: {name}')
                # Keep aspect ratio; bound dataset disk/decode costs without altering normalized boxes.
                h, w = frame.shape[:2]
                if max(h, w) > 960:
                    frame = cv2.resize(frame, (round(w*960/max(h, w)), round(h*960/max(h, w))), interpolation=cv2.INTER_AREA)
                add(split, f'ctx_{stem}.jpg', frame, labels, f'{SOURCE}/tree/{REVISION}#{name}', f'ctx:{stem}')
        with (output / 'sources.csv').open('w', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=FIELDS)
            writer.writeheader()
            writer.writerows(rows)
        (output / 'dataset.yaml').write_text(yaml.safe_dump(dict(path='.', **{s: f'images/{s}' for s in SPLITS}, names=dict(enumerate(NAMES))), sort_keys=False))
        (output / 'ATTRIBUTION.md').write_text(
            '# Public dataset attribution\n\n'
            'Weapon source: Ashish / Roboflow test-y7rj3, mirrored by fcakyon. CC BY 4.0.\n'
            'https://huggingface.co/datasets/fcakyon/gun-object-detection\n'
            'https://universe.roboflow.com/ashish-cuamw/test-y7rj3\n\n'
            'CTX-UXO: M. Craioveanu, G. Stamatescu and D. Popescu / UXO-Politehnica-Bucharest. CC BY 4.0.\n'
            f'{SOURCE}\nDOI: https://doi.org/10.21227/cwnm-de53\n\n'
            'License: https://creativecommons.org/licenses/by/4.0/\n\n'
            'Changes: combined datasets, mapped classes, resized CTX images to at most 960 pixels, '
            'removed exact duplicates and invalid boxes. Upstream weapon preparation exclusions remain. '
            'No endorsement by dataset authors is implied.\n')
        limitations = [
            'Upstream annotations have not been exhaustively manually reviewed here; missing or incorrect labels can remain.',
            'other_ordnance groups projectiles, rockets, mines, cartridges, magazines and fuses; not all are explosive.',
            'bomb covers the published mortar, aviation and antisubmarine bomb examples, not arbitrary concealed or improvised devices.',
            'RGB appearance cannot establish explosive chemistry, contents, authenticity or whether ordnance is live.',
            'Published partitions and exact-pixel deduplication are used; source-scene IDs are unavailable and scene leakage can remain.',
            'CCTV generalization and false alarms per camera-hour are not established; no dedicated reviewed negative-scene collection.',
            'Weapon images were stretched to 416x416 upstream. CTX-UXO depicts close-up field ordnance, not representative indoor CCTV.',
        ]
        save_json(output / 'preparation.json', dict(source=SOURCE, revision=REVISION, archive_sha256=SHA256,
                  starter_dataset_sha256=starter_report['dataset_sha256'], mapping=MAPPING, names=NAMES,
                  statistics=dict(stats), exclusions=excluded, limitations=limitations))
        report = audit(output / 'dataset.yaml', imgsz=512, allow_upstream=True)
        save_json(output / 'dataset-audit.json', report)
        if not report['valid']:
            raise ValueError('Merged dataset audit failed: ' + str(report['errors'][:10]))
        print(f'Ready: {output / "dataset.yaml"}\n{dict(stats)}', flush=True)
        return output / 'dataset.yaml'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--starter', type=Path, default=ROOT / 'data/weapon_starter')
    parser.add_argument('--cache', type=Path, default=ROOT / 'data/weapon_downloads/ctx-uxo')
    parser.add_argument('--output', type=Path, default=ROOT / 'data/weapon_ordnance')
    parser.add_argument('--offline', action='store_true')
    args = parser.parse_args()
    if not args.offline:
        base = f'{SOURCE}/resolve/{REVISION}/'
        download(base + 'README.md', args.cache / 'README.md', max_bytes=100000)
        download_archive(args.cache)
    prepare(args.starter, args.cache / 'CTX-UXO.zip', args.output)


if __name__ == '__main__':
    main()
