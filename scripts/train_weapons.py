"""Prepare and train local weapon-detector candidates, without activating them."""
import argparse
from collections import Counter
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
SPLITS = ('train', 'val', 'test')
CLASSES = ['gun', 'knife', 'grenade', 'explosion']
IMAGE_TYPES = {'.jpg', '.jpeg', '.png', '.bmp', '.webp'}
FIELDS = ['image', 'source_group', 'origin', 'usage_rights', 'reviewed']
FINE_TUNE_OPTIONS = dict(optimizer='AdamW', lr0=0.0001, lrf=0.1, warmup_epochs=1.0,
                         warmup_bias_lr=0.0, mosaic=0.0)


def save_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, default=lambda item: item.item()) + '\n')


def artifact_digest(path):
    """Checksum a file or a deterministically ordered exported model directory."""
    path = Path(path)
    digest = hashlib.sha256()
    files = sorted(p for p in path.rglob('*') if p.is_file()) if path.is_dir() else [path]
    for file in files:
        if path.is_dir():
            digest.update(file.relative_to(path).as_posix().encode() + b'\0')
        with file.open('rb') as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                digest.update(chunk)
    return digest.hexdigest()


def initialize(directory):
    """Create an empty dataset; an empty label explicitly means reviewed negative."""
    import yaml
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    for split in SPLITS:
        for kind in ('images', 'labels'):
            (directory / kind / split).mkdir(parents=True, exist_ok=True)
    config = directory / 'dataset.yaml'
    if not config.exists():
        config.write_text(yaml.safe_dump(dict(path='.', **{split: f'images/{split}' for split in SPLITS},
                                             names=dict(enumerate(CLASSES))), sort_keys=False))
    manifest = directory / 'sources.csv'
    if not manifest.exists():
        with manifest.open('w', newline='') as stream:
            csv.writer(stream).writerow(FIELDS)
    print(f'Dataset scaffold: {config}\nExisting files are preserved. Use check to inspect readiness.')


def audit(config_path, imgsz=640, allow_upstream=False):
    """Validate local YOLO directories and source separation before model loading."""
    import cv2
    import yaml
    config_path = Path(config_path).resolve()
    config = yaml.safe_load(config_path.read_text())
    if not isinstance(config, dict) or 'download' in config:
        raise ValueError('Use a local dataset YAML without a download script.')
    names = config.get('names')
    if isinstance(names, dict) and set(names) == set(range(len(names))):
        names = [names[index] for index in range(len(names))]
    if (not isinstance(names, list) or not names or any(not isinstance(name, str) or not name.strip() for name in names)
            or len(set(names)) != len(names)):
        raise ValueError('names must be a list or a contiguous zero-based class mapping.')
    root = (config_path.parent / config.get('path', '.')).resolve()
    errors, warnings, counts = [], [], {}
    if allow_upstream:
        warnings.append('Upstream annotations are accepted for this candidate; they have not been human-reviewed here.')
    preparation = root / 'preparation.json'
    if preparation.is_file():
        warnings.extend(json.loads(preparation.read_text()).get('limitations', []))
    records = {}
    manifest = root / 'sources.csv'
    if manifest.is_file():
        with manifest.open(newline='') as stream:
            reader = csv.DictReader(stream)
            if not set(FIELDS).issubset(reader.fieldnames or []):
                errors.append('sources.csv is missing required columns: ' + ', '.join(FIELDS))
            for row in reader:
                key = row.get('image', '')
                if key in records:
                    errors.append(f'Duplicate provenance row: {key}')
                records[key] = row
    else:
        errors.append('Missing sources.csv with origin, source grouping, rights and annotation review records.')
    groups, pixels, seen_images, seen_labels = {}, {}, set(), set()
    fingerprint = hashlib.sha256(config_path.read_bytes() + (manifest.read_bytes() if manifest.is_file() else b''))
    for split in SPLITS:
        # A deliberately narrow layout keeps image-to-label mapping unambiguous.
        if config.get(split) != f'images/{split}':
            raise ValueError(f'{split} must be images/{split}; use the layout created by init.')
        directory = root / 'images' / split
        images = sorted(p for p in directory.rglob('*') if p.suffix.lower() in IMAGE_TYPES and p.is_file())
        classes, negatives, small = Counter(), 0, 0
        if not images:
            errors.append(f'{split}: no images')
        for image in images:
            key = image.relative_to(root).as_posix()
            seen_images.add(key)
            record = records.get(key, {})
            if any(not str(record.get(field, '')).strip() for field in FIELDS):
                errors.append(f'{key}: incomplete provenance record')
            if record.get('reviewed', '').lower() not in ({'yes', 'upstream'} if allow_upstream else {'yes'}):
                errors.append(f'{key}: annotations must be reviewed (reviewed=yes)')
            group = record.get('source_group')
            if group:
                if group in groups and groups[group] != split:
                    errors.append(f'{key}: source group {group!r} leaks across splits')
                groups[group] = split
            frame = cv2.imread(str(image))
            if frame is None:
                errors.append(f'{key}: unreadable image')
                continue
            digest = hashlib.sha256(str(frame.shape).encode() + frame.tobytes()).hexdigest()
            if digest in pixels:
                errors.append(f'{key}: duplicate decoded image of {pixels[digest]}')
            pixels[digest] = key
            label = (root / 'labels' / split / image.relative_to(directory)).with_suffix('.txt')
            if label in seen_labels:
                errors.append(f'{key}: multiple images map to the same label file')
            seen_labels.add(label)
            if not label.is_file():
                errors.append(f'{key}: missing label; reviewed negatives require an empty .txt file')
                continue
            fingerprint.update(key.encode() + digest.encode() + label.read_bytes())
            rows = label.read_text().splitlines()
            if not any(row.strip() for row in rows):
                negatives += 1
            for index, row in enumerate(rows, 1):
                if not row.strip():
                    continue
                try:
                    fields = row.split()
                    if len(fields) != 5 or not fields[0].isdigit():
                        raise ValueError
                    cls = int(fields[0])
                    x, y, w, h = map(float, fields[1:])
                    if (not 0 <= cls < len(names) or not all(math.isfinite(v) for v in (x, y, w, h))
                            or not 0 < w <= 1 or not 0 < h <= 1
                            or x-w/2 < -1e-5 or x+w/2 > 1+1e-5 or y-h/2 < -1e-5 or y+h/2 > 1+1e-5):
                        raise ValueError
                    classes[names[cls]] += 1
                    height, width = frame.shape[:2]
                    small += min(w*width, h*height) * imgsz/max(width, height) < 8
                except ValueError:
                    errors.append(f'{label.relative_to(root)}:{index}: invalid class or normalized bounding box')
        orphaned = set((root / 'labels' / split).rglob('*.txt')) - seen_labels
        if orphaned:
            errors.append(f'{split}: {len(orphaned)} annotation files have no corresponding image')
        missing = set(names) - set(classes)
        if missing:
            errors.append(f'{split}: no boxes for classes: {", ".join(sorted(missing))}')
        if not negatives:
            warnings.append(f'{split}: add reviewed negative scenes, especially phones, tools and harmless lookalikes')
        if small:
            warnings.append(f'{split}: {small} boxes have a side under 8 pixels at input size {imgsz}; inspect crops/resolution')
        if classes and min(classes.values()) < 100:
            warnings.append(f'{split}: fewer than 100 boxes in at least one class; counts alone do not establish quality')
        counts[split] = dict(images=len(images), negative_images=negatives, boxes=dict(classes), tiny_boxes=int(small),
                             source_groups=len({r.get('source_group') for k, r in records.items() if k.startswith(f'images/{split}/')}))
    extra = set(records) - seen_images
    if extra:
        errors.append(f'sources.csv has {len(extra)} rows without matching images')
    canonical = dict(path=str(root), **{split: f'images/{split}' for split in SPLITS}, names=dict(enumerate(names)))
    return dict(valid=not errors, errors=errors, warnings=warnings, counts=counts, names=names,
                dataset_sha256=fingerprint.hexdigest(), config=canonical)


def configure_runtime(output, threads):
    os.environ['YOLO_CONFIG_DIR'] = str(output / '.runtime' / 'ultralytics')
    os.environ['MPLCONFIGDIR'] = str(output / '.runtime' / 'matplotlib')
    os.environ['YOLO_OFFLINE'] = 'true'
    os.environ['YOLO_AUTOINSTALL'] = 'false'
    Path(os.environ['YOLO_CONFIG_DIR']).mkdir(parents=True, exist_ok=True)
    import torch
    import cv2
    from ultralytics import YOLO, settings
    from ultralytics import utils
    torch.set_num_threads(threads)
    cv2.setNumThreads(1)
    settings.update({'sync': False, 'weights_dir': str(ROOT / 'models')})
    # CUDA AMP checks look up this module constant, initialized at import time.
    # Point them at the same verified base weights downloaded by the Colab setup.
    utils.WEIGHTS_DIR = ROOT / 'models'
    return YOLO


def run(args):
    import yaml
    report = audit(args.data, args.imgsz, args.allow_upstream_annotations)
    if args.command == 'check':
        print(json.dumps(report, indent=2))
        return 0 if report['valid'] else 2
    if not report['valid']:
        raise ValueError('Dataset not ready:\n' + '\n'.join(report['errors'][:30]))
    weights = args.weights.resolve()
    supported_file = weights.is_file() and weights.suffix in ('.pt', '.onnx', '.engine')
    supported_directory = weights.is_dir() and (weights.suffix == '.mlpackage' or weights.name.endswith(('_openvino_model', '_ncnn_model')))
    if not (supported_file or supported_directory):
        raise ValueError('Provide a local .pt/.onnx/.engine or exported OpenVINO/NCNN/CoreML directory.')
    if args.command in ('train', 'export') and weights.suffix != '.pt':
        raise ValueError('Training and export require a local PyTorch .pt checkpoint.')
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    data = output / 'dataset.yaml'
    data.write_text(yaml.safe_dump(report['config'], sort_keys=False))
    save_json(output / 'dataset-audit.json', report)
    recipe = dict(command=args.command, candidate_only=True, dataset_sha256=report['dataset_sha256'],
                  input_weights=str(weights), input_weights_sha256=artifact_digest(weights),
                  imgsz=args.imgsz, nms_free=True, device=args.device, cpu_threads=args.threads,
                  settings={key: str(value) if isinstance(value, Path) else value for key, value in vars(args).items()})
    if getattr(args, 'fine_tune', False):
        recipe['fine_tune_options'] = FINE_TUNE_OPTIONS
        recipe['optimizer_state_restored'] = False
    save_json(output / 'run.json', recipe)
    YOLO = configure_runtime(output, args.threads)
    model = YOLO(str(weights), task='detect')
    if args.command == 'train':
        if getattr(model.model.model[-1], 'one2one_cv2', None) is None:
            raise ValueError('This recipe targets YOLO26 with its end-to-end head; use local yolo26n.pt or yolo26s.pt.')
        if args.fine_tune:
            names = model.names
            names = [names[i] for i in range(len(names))] if isinstance(names, dict) else list(names)
            if names != report['names']:
                raise ValueError('Fine-tuning requires checkpoint classes in exactly the dataset class order.')
        model.train(data=str(data), epochs=args.epochs, batch=args.batch, imgsz=args.imgsz, device=args.device,
                    workers=args.workers, project=str(output), name='fit', exist_ok=False,
                    seed=42, deterministic=True, patience=args.patience,
                    close_mosaic=0 if args.fine_tune else min(10, args.epochs),
                    save_period=args.save_period, cache=False, plots=True,
                    amp=args.device != 'cpu', nms=False, **(FINE_TUNE_OPTIONS if args.fine_tune else {}))
        print(f'Candidate training finished: {model.trainer.best}. Evaluate before deployment.')
        return 0
    actual_names = model.names
    actual_names = [actual_names[index] for index in range(len(actual_names))] if isinstance(actual_names, dict) else list(actual_names)
    if actual_names != report['names']:
        raise ValueError('Checkpoint class order differs from dataset; refusing misleading evaluation/export.')
    if args.command == 'evaluate':
        metrics = model.val(data=str(data), split=args.split, imgsz=args.imgsz, device=args.device,
                            batch=1, workers=0, nms=False, rect=False, conf=.001,
                            project=str(output), name='metrics', plots=True)
        save_json(output / 'metrics.json', dict(split=args.split, aggregate=metrics.results_dict,
                  per_class=metrics.summary(), speed=metrics.speed,
                  note='Detection metrics, not false alarms per camera-hour or calibrated alert confidence.'))
        print(f'Evaluation saved: {output / "metrics.json"}')
    else:
        if getattr(model.model.model[-1], 'one2one_cv2', None) is None:
            raise ValueError('ONNX export requires a YOLO26 end-to-end candidate.')
        # Export beside a copy, preserving the source checkpoint and any prior exports.
        target = output / 'candidate.pt'
        shutil.copy2(weights, target)
        exported = YOLO(str(target), task='detect').export(format='onnx', imgsz=args.imgsz, batch=1,
                  dynamic=False, simplify=False, opset=17, nms=False, device='cpu')
        artifact = Path(exported)
        save_json(output / 'candidate.json', dict(candidate_only=True, names=report['names'], imgsz=args.imgsz,
                  format='onnx', precision='FP32', nms_free=True, file=artifact.name,
                  sha256=hashlib.sha256(artifact.read_bytes()).hexdigest(), dataset_sha256=report['dataset_sha256']))
        print(f'Candidate export: {artifact}. Re-evaluate this exact ONNX artifact before deployment.')
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    init = commands.add_parser('init', help='Create an empty local dataset scaffold')
    init.add_argument('--directory', type=Path, default=ROOT / 'data' / 'weapon_dataset')
    for command in ('check', 'train', 'evaluate', 'export'):
        item = commands.add_parser(command)
        item.add_argument('--data', type=Path, required=True)
        item.add_argument('--imgsz', type=int, default=640)
        item.add_argument('--allow-upstream-annotations', action='store_true',
                          help='Accept reviewed=upstream public labels for candidate experiments; not a human review claim')
        if command != 'check':
            item.add_argument('--weights', type=Path, default=ROOT / 'models' / 'yolo26n.pt' if command == 'train' else None,
                              required=command != 'train')
            item.add_argument('--output', type=Path, required=True, help='New directory for this candidate run')
            item.add_argument('--device', default='cpu', help='cpu, mps (Apple GPU), or CUDA device such as 0')
            item.add_argument('--threads', type=int, default=1)
        if command == 'train':
            item.add_argument('--epochs', type=int, default=100)
            item.add_argument('--batch', type=int, default=8)
            item.add_argument('--workers', type=int, default=0)
            item.add_argument('--patience', type=int, default=20, help='Early stopping patience; 0 runs all requested epochs')
            item.add_argument('--save-period', type=int, default=-1, help='Keep an additional checkpoint every N epochs; -1 disables')
            item.add_argument('--fine-tune', action='store_true', help='Continue trained weights with fresh AdamW, lr=0.0001 and mosaic disabled')
        if command == 'evaluate':
            item.add_argument('--split', choices=('val', 'test'), default='val')
    args = parser.parse_args(argv)
    if args.command == 'init':
        initialize(args.directory)
        return 0
    if args.imgsz < 320 or args.imgsz % 32:
        parser.error('--imgsz must be a multiple of 32, at least 320')
    if getattr(args, 'threads', 1) < 1 or getattr(args, 'epochs', 1) < 1 or getattr(args, 'batch', 1) < 1 or getattr(args, 'workers', 0) < 0:
        parser.error('threads, epochs and batch must be positive; workers must be nonnegative')
    save_period = getattr(args, 'save_period', -1)
    if getattr(args, 'patience', 0) < 0 or save_period == 0 or save_period < -1:
        parser.error('patience must be nonnegative; save-period must be -1 or positive')
    try:
        return run(args)
    except (ValueError, OSError) as exc:
        parser.exit(2, f'{exc}\n')


if __name__ == '__main__':
    sys.exit(main())
