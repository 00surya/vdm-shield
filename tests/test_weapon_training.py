import csv
import json

import cv2
import numpy as np
import pytest

from scripts.train_weapons import audit, initialize, main, FIELDS, SPLITS


def dataset(root):
    initialize(root)
    rows = []
    for split_index, split in enumerate(SPLITS):
        for index in range(5):
            image = root / 'images' / split / f'{index}.png'
            pixels = np.random.default_rng(split_index*10+index).integers(0, 256, (64, 96, 3), dtype=np.uint8)
            assert cv2.imwrite(str(image), pixels)
            label = root / 'labels' / split / f'{index}.txt'
            label.write_text(f'{index} 0.5 0.5 0.25 0.5\n' if index < 4 else '')
            rows.append(dict(image=image.relative_to(root).as_posix(), source_group=f'camera-{split}',
                             origin='generated test fixture', usage_rights='test fixture only', reviewed='yes'))
    with (root / 'sources.csv').open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return root / 'dataset.yaml'


def test_scaffold_is_empty_and_training_fails_before_loading_model(tmp_path):
    initialize(tmp_path)
    assert not audit(tmp_path / 'dataset.yaml')['valid']
    with pytest.raises(SystemExit) as error:
        main(['train', '--data', str(tmp_path/'dataset.yaml'), '--output', str(tmp_path/'candidate')])
    assert error.value.code == 2
    assert not (tmp_path / 'candidate').exists()


def test_audit_counts_reviewed_negatives_and_is_stable(tmp_path):
    config = dataset(tmp_path)
    report = audit(config)
    assert report['valid'], report['errors']
    assert report['counts']['train']['images'] == 5
    assert report['counts']['test']['negative_images'] == 1
    assert report['counts']['val']['boxes'] == dict(gun=1, knife=1, grenade=1, explosion=1)
    assert report['dataset_sha256'] == audit(config)['dataset_sha256']
    initialize(tmp_path)
    assert audit(config)['dataset_sha256'] == report['dataset_sha256']


@pytest.mark.parametrize('row', ['9 0.5 0.5 0.2 0.2', '0 nan 0.5 0.2 0.2', '0 0.1 0.5 0.8 0.2', '0 0.5 0.5 0 0.2', '0 0.5'])
def test_bad_bounding_boxes_are_rejected(tmp_path, row):
    config = dataset(tmp_path)
    (tmp_path / 'labels/train/0.txt').write_text(row)
    report = audit(config)
    assert not report['valid'] and any('invalid class' in error for error in report['errors'])


def test_missing_label_is_not_silently_treated_as_a_negative(tmp_path):
    config = dataset(tmp_path)
    (tmp_path / 'labels/train/4.txt').unlink()
    assert any('missing label' in error for error in audit(config)['errors'])


def test_source_groups_and_identical_pixels_cannot_leak_between_splits(tmp_path):
    config = dataset(tmp_path)
    manifest = tmp_path / 'sources.csv'
    manifest.write_text(manifest.read_text().replace('camera-val', 'camera-train'))
    pixels = cv2.imread(str(tmp_path / 'images/train/0.png'))
    cv2.imwrite(str(tmp_path / 'images/test/1.png'), pixels)
    errors = audit(config)['errors']
    assert any('leaks across splits' in error for error in errors)
    assert any('duplicate decoded image' in error for error in errors)


def test_unreviewed_provenance_and_unsupported_download_scripts_are_rejected(tmp_path):
    config = dataset(tmp_path)
    manifest = tmp_path / 'sources.csv'
    manifest.write_text(manifest.read_text().replace(',yes', ',no'))
    assert any('must be reviewed' in error for error in audit(config)['errors'])
    config.write_text(config.read_text() + '\ndownload: echo unwanted\n')
    with pytest.raises(ValueError, match='without a download script'):
        audit(config)


@pytest.mark.parametrize('fine_tune', [False, True])
def test_training_uses_a_separate_candidate_and_validated_absolute_dataset(tmp_path, monkeypatch, fine_tune):
    import scripts.train_weapons as module
    from types import SimpleNamespace
    config = dataset(tmp_path / 'dataset')
    weights = tmp_path / 'base.pt'
    weights.write_bytes(b'fixture')
    output = tmp_path / 'candidate'
    calls = []

    class FakeYOLO:
        names = dict(enumerate(module.CLASSES))
        def __init__(self, *args, **kwargs):
            # Current YOLO26 checkpoints have both heads but default end2end=False.
            self.model = SimpleNamespace(end2end=False, model=[SimpleNamespace(one2one_cv2=object())])
            self.trainer = SimpleNamespace(best=output/'fit/weights/best.pt')
        def train(self, **kwargs):
            calls.append(kwargs)

    monkeypatch.setattr(module, 'configure_runtime', lambda *args: FakeYOLO)
    epochs = 20 if fine_tune else 30
    assert main(['train', '--data', str(config), '--weights', str(weights), '--output', str(output),
                 '--epochs', str(epochs), '--patience', '0', '--save-period', '5', '--device', 'mps',
                 *(['--fine-tune'] if fine_tune else [])]) == 0
    assert calls[0]['nms'] is False and calls[0]['device'] == 'mps'
    assert calls[0]['close_mosaic'] == (0 if fine_tune else 10)
    assert calls[0]['epochs'] == epochs and calls[0]['patience'] == 0 and calls[0]['save_period'] == 5
    if fine_tune:
        assert calls[0]['optimizer'] == 'AdamW' and calls[0]['lr0'] == 0.0001
        assert calls[0]['warmup_bias_lr'] == 0 and calls[0]['mosaic'] == 0
    assert audit(output/'dataset.yaml')['valid']
    assert json.loads((output/'run.json').read_text())['candidate_only']
    assert weights.read_bytes() == b'fixture'
