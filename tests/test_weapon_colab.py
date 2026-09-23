"""Data integrity and deployable-export contracts for the standalone training kit."""
import ast
import csv
import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace
import zipfile

import cv2
import numpy as np
import pytest
import yaml

from scripts import prepare_weapon_data as prepare
from scripts import export_weapons as exporter
from scripts.train_weapons import artifact_digest, audit


def archives(root, monkeypatch):
    root.mkdir()
    checksums = {}
    shared = None
    for split in ('valid', 'train'):
        images, annotations = [], []
        with zipfile.ZipFile(root / f'{split}.zip', 'w') as archive:
            for index in range(90):
                rng = np.random.default_rng(index + (1000 if split == 'train' else 0))
                frame = rng.integers(0, 255, (24, 32, 3), dtype=np.uint8)
                if index == 0 and split == 'valid':
                    shared = frame
                if index == 0 and split == 'train':
                    frame = shared  # identical pixels, distinct filename, across splits
                filename = f'{split}-{index}.rf.fixture.png'
                images.append(dict(id=index, file_name=filename, width=32, height=24))
                annotations.append(dict(id=index, image_id=index, category_id=[4, 9, 12][index % 3],
                                        bbox=[8, 6, 16, 12], iscrowd=0))
                archive.writestr(filename, cv2.imencode('.png', frame)[1].tobytes())
            archive.writestr('_annotations.coco.json', json.dumps(dict(images=images, annotations=annotations,
                categories=[dict(id=4, name='pistol'), dict(id=9, name='knife'), dict(id=12, name='grenade')])))
        path = root / f'{split}.zip'
        checksums[split] = (artifact_digest(path), path.stat().st_size)
    monkeypatch.setattr(prepare, 'ARCHIVES', checksums)
    return root


def test_real_coco_mapping_duplicates_and_reproducible_splits(tmp_path, monkeypatch):
    cache = archives(tmp_path / 'cache', monkeypatch)
    first = prepare.prepare(cache, tmp_path / 'first')
    second = prepare.prepare(cache, tmp_path / 'second')
    report = audit(first, allow_upstream=True)
    assert report['valid']
    assert report['names'] == ['gun', 'knife', 'grenade']
    assert report['dataset_sha256'] == audit(second, allow_upstream=True)['dataset_sha256']
    assert not audit(first)['valid']  # downloading did not claim human review
    metadata = json.loads((first.parent / 'preparation.json').read_text())
    assert metadata['statistics']['duplicate_pixels'] == 1
    assert sum(split['images'] for split in report['counts'].values()) == 179
    for path in first.parent.glob('labels/*/*.txt'):
        cls, x, y, w, h = path.read_text().split()
        assert int(cls) in range(3)
        assert [float(x), float(y), float(w), float(h)] == [.5, .5, .5, .5]
    assert all(row['reviewed'] == 'upstream' for row in csv.DictReader((first.parent / 'sources.csv').open()))


def test_checksum_failure_creates_no_prepared_output(tmp_path, monkeypatch):
    cache = archives(tmp_path / 'cache', monkeypatch)
    (cache / 'train.zip').write_bytes(b'corrupt')
    with pytest.raises(ValueError, match='incorrect pinned archive'):
        prepare.prepare(cache, tmp_path / 'output')
    assert not (tmp_path / 'output').exists()


@pytest.mark.parametrize('path', ['../escape.jpg', '/absolute.jpg', 'C:/absolute.jpg', 'a\\b.jpg'])
def test_archive_unsafe_paths_are_rejected(path):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w') as archive:
        archive.writestr(path, b'x')
        archive.writestr('_annotations.coco.json', '{}')
    with zipfile.ZipFile(stream) as archive, pytest.raises(ValueError, match='Unsafe archive path'):
        prepare.inspect_zip(archive)


def test_coco_clipping_and_invalid_boxes():
    row, clipped = prepare.yolo_box(dict(category_id=4, bbox=[-2, 0, 12, 10]), 20, 20, {4: 0})
    assert clipped and row == '0 0.250000000 0.250000000 0.500000000 0.500000000'
    for box in ([0, 0, -1, 10], [0, 0, float('nan'), 10], [30, 30, 10, 10]):
        with pytest.raises(ValueError):
            prepare.yolo_box(dict(category_id=4, bbox=box), 20, 20, {4: 0})


def test_cached_download_rechecks_checksum(tmp_path):
    path = tmp_path / 'weights.pt'
    path.write_bytes(b'wrong')
    with pytest.raises(ValueError, match='Cached checksum mismatch'):
        prepare.download('https://unused.example/', path, '0' * 64)
    assert path.read_bytes() == b'wrong'


@pytest.mark.parametrize('target', list(exporter.PROFILES))
def test_profiles_preserve_head_classes_and_calibrate_only_training(tmp_path, monkeypatch, target):
    cache = archives(tmp_path / 'cache', monkeypatch)
    data = prepare.prepare(cache, tmp_path / 'dataset')
    weights = tmp_path / 'best.pt'
    weights.write_bytes(b'test checkpoint')
    output = tmp_path / target
    calls = []

    class FakeModel:
        names = {0: 'gun', 1: 'knife', 2: 'grenade'}
        model = SimpleNamespace(model=[SimpleNamespace(one2one_cv2=object())])
        def __init__(self, path, task):
            assert Path(path).read_bytes() == weights.read_bytes()
        def export(self, **options):
            calls.append(options)
            artifact = output / 'fake-artifact.onnx'
            artifact.write_bytes(b'export')
            return artifact

    monkeypatch.setattr(exporter, 'check_target', lambda *args: None)
    monkeypatch.setattr(exporter, 'configure_runtime', lambda *args: FakeModel)
    assert exporter.main(['--weights', str(weights), '--data', str(data), '--output', str(output),
                          '--target', target, '--allow-upstream-annotations']) == 0
    options = calls[0]
    assert options['nms'] is False and options['batch'] == 1 and not options['dynamic']
    if target == 'openvino-int8':
        config = yaml.safe_load(Path(options['data']).read_text())
        assert config['train'] == config['val'] == 'images/train'
        assert 'test' not in config and options['split'] == 'train'
    else:
        assert 'data' not in options
    manifest = json.loads((output / 'candidate.json').read_text())
    assert manifest['candidate_only'] and manifest['validation_required']
    assert manifest['names'] == ['gun', 'knife', 'grenade']
    assert weights.read_bytes() == b'test checkpoint'


def test_coreml_is_not_misrepresented_as_a_colab_step(monkeypatch):
    monkeypatch.setattr(exporter.platform, 'system', lambda: 'Linux')
    with pytest.raises(ValueError, match='on a Mac'):
        exporter.check_target('coreml-fp16', 'cpu')


def test_notebook_and_bundle_contain_current_standalone_sources():
    root = Path(__file__).resolve().parents[1]
    notebook = json.loads((root / 'notebooks/weapon_training_colab.ipynb').read_text())
    assert notebook['nbformat'] == 4
    embedded = None
    for cell in notebook['cells']:
        if cell['cell_type'] == 'code':
            tree = ast.parse(''.join(cell['source']))
            for statement in tree.body:
                if isinstance(statement, ast.Assign) and any(isinstance(target, ast.Name) and target.id == 'EMBEDDED_FILES' for target in statement.targets):
                    embedded = ast.literal_eval(statement.value)
            assert cell['outputs'] == [] and cell['execution_count'] is None
    assert embedded is not None
    with zipfile.ZipFile(root / 'artifacts/weapon-training-kit.zip') as archive:
        assert archive.testzip() is None
        for name, checksum in notebook['metadata']['embedded_source_sha256'].items():
            source = (root / name).read_bytes()
            assert hashlib.sha256(source).hexdigest() == checksum
            assert embedded[name].encode() == source
            assert archive.read(name) == source
