"""Download pinned public COCO data and prepare an auditable three-class YOLO starter."""
import argparse
from collections import Counter, defaultdict
import csv
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import sys
import time
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.train_weapons import FIELDS, SPLITS, artifact_digest, audit, save_json

REVISION = '0c8e46cbfe8edf71e592f495face94ba22155b46'
SOURCE = 'https://huggingface.co/datasets/fcakyon/gun-object-detection'
BASE = f'{SOURCE}/resolve/{REVISION}/'
ARCHIVES = {
    'train': ('b411ecb8ca9f4d54f3a1fb1899db14832ccef2f3c44c910ffaa1f2649d87fe79', 73680397),
    'valid': ('d676264a3e040a71eb58ea71a4cd16391537c9eef84531df3d198311a1d86723', 18650193),
}
WEIGHTS_URL = 'https://github.com/ultralytics/assets/releases/download/v8.4.0/yolo26n.pt'
WEIGHTS_SHA = '9b09cc8bf347f0fc8a5f7657480587f25db09b34bf33b0652110fb03a8ad4fef'
NAMES = ['gun', 'knife', 'grenade']
MAPPING = {'pistol': 0, 'rifle': 0, 'knife': 1, 'grenade': 2}
KNOWN_INCOMPLETE = {'00077a3cc51da6fab9ad7c78dd575e1a95fc54cfbeb8cac99d9ddff2904cef8d'}
LIMITATIONS = [
    'Public starter labels are upstream annotations, not human-reviewed by this project.',
    'A visual spot-check found an unlabeled rifle beside a labeled grenade. That known image is excluded; other missing or incorrect boxes may remain.',
    'Original camera/video identifiers are unavailable. Filename families and exact pixels are grouped; near-duplicate or scene leakage can remain.',
    'The published validation set is reserved as test; validation is a deterministic 15% holdout from published training families.',
    'Only gun, knife and grenade are covered. No explosion or generic bomb class.',
    'Source images were stretched to 416x416; larger training input cannot recover lost detail. CCTV quality and false-alarm performance are unproven.',
]


def download(url, destination, checksum=None, max_bytes=100_000_000):
    """Stream to a temporary file, retry, verify, then publish; never execute remote code."""
    destination = Path(destination)
    if destination.is_file():
        if checksum and artifact_digest(destination) != checksum:
            raise ValueError(f'Cached checksum mismatch: {destination}; move it aside and retry.')
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + '.part')
    for attempt in range(3):
        try:
            request = urllib.request.Request(url, headers={'User-Agent': 'VDM-weapon-training/1.0'})
            size = 0
            with urllib.request.urlopen(request, timeout=60) as source, temporary.open('wb') as target:
                while chunk := source.read(1024 * 1024):
                    size += len(chunk)
                    if size > max_bytes:
                        raise ValueError(f'Download exceeded size limit: {destination.name}')
                    target.write(chunk)
            if checksum and artifact_digest(temporary) != checksum:
                raise ValueError(f'Download checksum mismatch: {destination.name}')
            temporary.replace(destination)
            print(f'Downloaded {destination.name}: {size / 1e6:.1f} MB', flush=True)
            return destination
        except (OSError, ValueError):
            temporary.unlink(missing_ok=True)
            if attempt == 2:
                raise
            time.sleep(attempt + 1)


def download_source(cache):
    cache = Path(cache)
    for split, (checksum, size) in ARCHIVES.items():
        download(BASE + f'data/{split}.zip', cache / f'{split}.zip', checksum, size)
    for name in ('README.md', 'README.dataset.txt', 'README.roboflow.txt'):
        download(BASE + name, cache / name, max_bytes=1_000_000)


def inspect_zip(archive):
    infos = archive.infolist()
    if len(infos) > 20_000 or sum(info.file_size for info in infos) > 2_000_000_000:
        raise ValueError('Archive is larger than the supported starter dataset.')
    seen = set()
    for info in infos:
        path = PurePosixPath(info.filename)
        if path.is_absolute() or '..' in path.parts or '\\' in info.filename or ':' in info.filename:
            raise ValueError('Unsafe archive path: ' + info.filename)
        if info.filename in seen or info.file_size > 50_000_000:
            raise ValueError('Duplicate member or oversized file in archive.')
        seen.add(info.filename)
    candidates = [name for name in seen if name.endswith('_annotations.coco.json')]
    if len(candidates) != 1:
        raise ValueError('Expected exactly one _annotations.coco.json in each archive.')
    return candidates[0]


def yolo_box(annotation, width, height, category_map):
    if annotation.get('iscrowd', 0):
        raise ValueError('Crowd annotations need manual review.')
    category = category_map[annotation['category_id']]
    x, y, w, h = map(float, annotation['bbox'])
    if not all(math.isfinite(value) for value in (x, y, w, h)) or w <= 0 or h <= 0:
        raise ValueError('Invalid COCO bounding box.')
    # Some upstream boxes slightly cross image edges. Clip the visible extent and record it.
    x1, y1, x2, y2 = max(0., x), max(0., y), min(float(width), x+w), min(float(height), y+h)
    if x2 <= x1 or y2 <= y1:
        raise ValueError('COCO box lies outside the image.')
    clipped = (x1, y1, x2, y2) != (x, y, x+w, y+h)
    row = f'{category} {(x1+x2)/(2*width):.9f} {(y1+y2)/(2*height):.9f} {(x2-x1)/width:.9f} {(y2-y1)/height:.9f}'
    return row, clipped


def prepare(cache, output, seed=42):
    import cv2
    import numpy as np
    import yaml
    cache, output = Path(cache), Path(output).resolve()
    # Fail before creating output when an archive is missing, incomplete, or tampered with.
    for split, (checksum, _) in ARCHIVES.items():
        path = cache / f'{split}.zip'
        if not path.is_file() or artifact_digest(path) != checksum:
            raise ValueError(f'Missing or incorrect pinned archive: {path}')
    output.mkdir(parents=True, exist_ok=False)
    for split in SPLITS:
        for kind in ('images', 'labels'):
            (output / kind / split).mkdir(parents=True)
    records, skipped, pixels, family_splits = [], [], {}, {}
    stats = Counter()
    # Held-out images take precedence over copies in the upstream training archive.
    for original_split in ('valid', 'train'):
        with zipfile.ZipFile(cache / f'{original_split}.zip') as archive:
            annotation_file = inspect_zip(archive)
            coco = json.loads(archive.read(annotation_file))
            categories = {item['id']: MAPPING[item['name'].lower()] for item in coco['categories']}
            by_image = defaultdict(list)
            image_ids = {item['id'] for item in coco['images']}
            if len(image_ids) != len(coco['images']):
                raise ValueError('Duplicate COCO image IDs.')
            for annotation in coco['annotations']:
                if annotation['image_id'] not in image_ids or annotation['category_id'] not in categories:
                    raise ValueError('Annotation references an unknown image/category.')
                by_image[annotation['image_id']].append(annotation)
            for item in sorted(coco['images'], key=lambda item: item['file_name']):
                filename = item['file_name']
                member = str(PurePosixPath(annotation_file).parent / filename)
                suffix = PurePosixPath(filename).suffix.lower()
                if suffix not in ('.jpg', '.jpeg', '.png', '.webp'):
                    raise ValueError(f'Unsupported image extension: {filename}')
                encoded = archive.read(member)
                frame = cv2.imdecode(np.frombuffer(encoded, dtype=np.uint8), cv2.IMREAD_COLOR)
                if frame is None or frame.shape[:2] != (item['height'], item['width']):
                    raise ValueError(f'COCO/image dimensions disagree: {filename}')
                digest = hashlib.sha256(str(frame.shape).encode() + frame.tobytes()).hexdigest()
                if digest in KNOWN_INCOMPLETE:
                    skipped.append(dict(file=filename, upstream_split=original_split, reason='known_incomplete_annotation'))
                    stats['known_incomplete_annotation'] += 1
                    continue
                # Roboflow .rf.<hash> variants of one original belong to one family.
                family = PurePosixPath(filename).name.split('.rf.')[0]
                group = hashlib.sha256(family.encode()).hexdigest()
                held_out = int(hashlib.sha256(f'{seed}:{family}'.encode()).hexdigest(), 16) / 2**256 < .15
                split = 'test' if original_split == 'valid' else ('val' if held_out else 'train')
                reason = 'duplicate_pixels' if digest in pixels else None
                if group in family_splits and family_splits[group] != split:
                    reason = 'family_in_heldout_split'
                if reason:
                    skipped.append(dict(file=filename, upstream_split=original_split, reason=reason))
                    stats[reason] += 1
                    continue
                rows = []
                for annotation in by_image[item['id']]:
                    row, clipped = yolo_box(annotation, item['width'], item['height'], categories)
                    rows.append(row)
                    stats['clipped_boxes'] += int(clipped)
                rows = sorted(set(rows))
                key = f'images/{split}/{digest}{suffix}'
                (output / key).write_bytes(encoded)
                (output / 'labels' / split / f'{digest}.txt').write_text('\n'.join(rows) + ('\n' if rows else ''))
                records.append(dict(image=key, source_group='filename-family:' + group,
                    origin=f'{SOURCE}/tree/{REVISION}#{original_split}/{filename}',
                    usage_rights='CC BY 4.0 declared by upstream; see attribution/', reviewed='upstream'))
                pixels[digest], family_splits[group] = key, split
                stats[f'{split}_images'] += 1
    with (output / 'sources.csv').open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(records)
    config = output / 'dataset.yaml'
    config.write_text(yaml.safe_dump(dict(path='.', **{split: f'images/{split}' for split in SPLITS},
                                         names=dict(enumerate(NAMES))), sort_keys=False))
    attribution = output / 'attribution'
    attribution.mkdir()
    for name in ('README.md', 'README.dataset.txt', 'README.roboflow.txt'):
        if (cache / name).is_file():
            (attribution / name).write_bytes((cache / name).read_bytes())
    (attribution / 'CHANGES.txt').write_text(
        f'Source: {SOURCE}\nRevision: {REVISION}\nOriginal creator: ashish (Roboflow); mirror: fcakyon.\n'
        'Declared license: CC BY 4.0 — https://creativecommons.org/licenses/by/4.0/\n'
        'Changes: COCO to YOLO; pistol/rifle merged as gun; known incomplete sample excluded; edge boxes clipped; exact decoded duplicates removed; '
        'filename families separated; validation carved from training; original validation reserved as test.\n')
    save_json(output / 'preparation.json', dict(source=SOURCE, revision=REVISION, archive_sha256=ARCHIVES,
        seed=seed, names=NAMES, statistics=dict(stats), skipped=skipped, limitations=LIMITATIONS))
    report = audit(config, allow_upstream=True)
    save_json(output / 'dataset-audit.json', report)
    print(json.dumps(dict(valid=report['valid'], counts=report['counts'], statistics=dict(stats)), indent=2))
    if not report['valid']:
        raise ValueError('Prepared data failed checks; inspect dataset-audit.json. ' + '; '.join(report['errors'][:10]))
    return config


def preview(config, destination, count=12):
    """Show one deterministic sample per class, then fill the contact sheet."""
    import cv2
    import numpy as np
    import yaml
    config = Path(config).resolve()
    data = yaml.safe_load(config.read_text())
    root = (config.parent / data.get('path', '.')).resolve()
    paths = sorted((root / 'images/train').glob('*'))
    selected = []
    for index in range(len(data['names'])):
        for image in paths:
            label = root / 'labels/train' / (image.stem + '.txt')
            if image not in selected and any(row.startswith(f'{index} ') for row in label.read_text().splitlines()):
                selected.append(image)
                break
    selected += [image for image in paths if image not in selected][:max(0, count-len(selected))]
    tiles = []
    for image in selected[:count]:
        frame = cv2.imread(str(image))
        height, width = frame.shape[:2]
        for row in (root / 'labels/train' / (image.stem + '.txt')).read_text().splitlines():
            cls, x, y, w, h = map(float, row.split())
            p1, p2 = (int((x-w/2)*width), int((y-h/2)*height)), (int((x+w/2)*width), int((y+h/2)*height))
            cv2.rectangle(frame, p1, p2, (0, 220, 255), 2)
            cv2.putText(frame, data['names'][int(cls)], (p1[0], max(15, p1[1])), cv2.FONT_HERSHEY_SIMPLEX, .5, (0, 220, 255), 1)
        tiles.append(cv2.resize(frame, (320, 320)))
    if not tiles:
        raise ValueError('No training images to preview.')
    while len(tiles) % 4:
        tiles.append(np.zeros((320, 320, 3), dtype=np.uint8))
    sheet = np.vstack([np.hstack(tiles[index:index+4]) for index in range(0, len(tiles), 4)])
    if not cv2.imwrite(str(destination), sheet):
        raise OSError(f'Could not write preview: {destination}')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache', type=Path, default=ROOT / 'data/weapon_downloads')
    parser.add_argument('--output', type=Path, default=ROOT / 'data/weapon_starter')
    parser.add_argument('--seed', type=int, default=42)
    parser.add_argument('--offline', action='store_true', help='Use verified cached archives; do not download')
    parser.add_argument('--weights', type=Path, help='Also download the checksum-pinned official COCO yolo26n.pt here')
    args = parser.parse_args(argv)
    try:
        if args.output.exists():
            raise ValueError('Output already exists; use it as-is or choose a new directory. No overwrite.')
        if not args.offline:
            download_source(args.cache)
        if args.weights:
            if args.offline:
                if not args.weights.is_file() or artifact_digest(args.weights) != WEIGHTS_SHA:
                    raise ValueError('Offline mode requires the pinned pretrained weights already present.')
            else:
                download(WEIGHTS_URL, args.weights, WEIGHTS_SHA, 6_000_000)
        config = prepare(args.cache, args.output, args.seed)
        preview(config, args.output / 'preview.jpg')
        print(f'Ready for candidate training: {config}\nReview preview.jpg and preparation.json before using the dataset.')
        return 0
    except (ValueError, KeyError, OSError, zipfile.BadZipFile) as exc:
        parser.exit(2, f'{exc}\n')


if __name__ == '__main__':
    sys.exit(main())
