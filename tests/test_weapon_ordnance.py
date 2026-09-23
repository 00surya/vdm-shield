"""Contracts for the public ordnance mapping and exact-epoch experiment."""
import pytest
import csv
import io
import json
import zipfile
import cv2
import numpy as np
import yaml

from scripts.prepare_weapon_ordnance_data import MAPPING, NAMES, remap_labels
from scripts.run_weapon_experiment import completed_epochs, continuation_info
from scripts.train_weapons import FIELDS, SPLITS, artifact_digest, audit


def test_continuation_preserves_lineage_and_rejects_changed_data_or_partial_training(tmp_path):
    run = tmp_path / 'previous'
    (run / 'training/fit/weights').mkdir(parents=True)
    checkpoint = run / 'training/fit/weights/last.pt'
    checkpoint.write_bytes(b'locally trained weights')
    (run / 'status.json').write_text(json.dumps(dict(training_complete=True, epochs_requested=30)))
    (run / 'training/run.json').write_text(json.dumps(dict(dataset_sha256='original-dataset')))
    results = run / 'training/fit/results.csv'
    results.write_text('epoch,time\n' + ''.join(f'{i},{i*60}\n' for i in range(1,31)))
    report = dict(valid=True, dataset_sha256='original-dataset')
    info = continuation_info(run, report)
    assert info['prior_epochs'] == 30 and info['optimizer_state_restored'] is False
    assert info['input_checkpoint_sha256'] == artifact_digest(checkpoint)
    with pytest.raises(ValueError, match='same audited'):
        continuation_info(run, dict(valid=True, dataset_sha256='changed-dataset'))
    results.write_text('epoch,time\n1,60\n')
    with pytest.raises(ValueError, match='fully completed'):
        continuation_info(run, report)


def test_short_http_transfer_resumes_without_discarding_saved_bytes(tmp_path, monkeypatch):
    from scripts import prepare_weapon_ordnance_data as module
    payload = b'abcdefghij'
    target = tmp_path / 'part'
    target.with_suffix('.tmp').write_bytes(payload[:2])
    requested = []

    class Response(io.BytesIO):
        status = 206

    def open_range(request, timeout):
        first, last = map(int, request.get_header('Range').removeprefix('bytes=').split('-'))
        requested.append((first, last))
        # The first connection closes early, despite the correct response header.
        response = Response(payload[first:first+3] if len(requested) == 1 else payload[first:last+1])
        response.headers = {'Content-Range': f'bytes {first}-{last}/{len(payload)}'}
        return response

    monkeypatch.setattr(module.urllib.request, 'urlopen', open_range)
    monkeypatch.setattr(module.time, 'sleep', lambda _: None)
    assert module.download_range('https://example.invalid/data?x=1', target, 0, 9, 10) == 10
    assert requested == [(2, 9), (5, 9)]
    assert target.read_bytes() == payload
    assert not target.with_suffix('.tmp').exists()


def test_wrong_http_range_cannot_damage_a_saved_partial(tmp_path, monkeypatch):
    from scripts import prepare_weapon_ordnance_data as module
    target = tmp_path / 'part'
    partial = target.with_suffix('.tmp')
    partial.write_bytes(b'ab')

    class Response(io.BytesIO):
        status = 200
        headers = {}

    monkeypatch.setattr(module.urllib.request, 'urlopen', lambda *a, **kw: Response(b'wrong bytes'))
    with pytest.raises(ValueError, match='did not honor'):
        module.download_range('https://example.invalid/data?x=1', target, 0, 9, 10)
    assert partial.read_bytes() == b'ab'
    assert not target.exists()


def test_every_upstream_box_is_retained_with_explicit_visual_class_mapping():
    names = list(MAPPING)
    source = '\n'.join(f'{i} 0.5 0.5 0.4 0.6' for i in range(len(names)))
    rows = remap_labels(source, names).splitlines()
    assert len(rows) == 12
    mapped = [NAMES[int(row.split()[0])] for row in rows]
    assert mapped.count('bomb') == 3
    assert mapped.count('grenade') == 1
    assert mapped.count('other_ordnance') == 8
    assert all(row.endswith('0.5 0.5 0.4 0.6') for row in rows)


@pytest.mark.parametrize('label', ['-1 0.5 0.5 0.4 0.4', '12 0.5 0.5 0.4 0.4',
                                   '0 nan 0.5 0.4 0.4', '0 0.1 0.5 0.9 0.4', '0 0.5'])
def test_invalid_annotations_cannot_become_training_boxes(label):
    with pytest.raises(ValueError):
        remap_labels(label, list(MAPPING))


def test_empty_labels_remain_empty_and_epoch_report_reads_actual_completed_epochs(tmp_path):
    assert remap_labels('', list(MAPPING)) == ''
    path = tmp_path / 'results.csv'
    path.write_text('epoch, time\n1, 40\n2, 80\n')
    assert completed_epochs(path) == [1, 2]


def test_merge_keeps_holdouts_and_all_classes_without_claiming_human_review(tmp_path, monkeypatch):
    from scripts import prepare_weapon_ordnance_data as module
    starter = tmp_path / 'starter'
    records = []
    for s, split in enumerate(SPLITS):
        for kind in ('images', 'labels'):
            (starter / kind / split).mkdir(parents=True)
        for cls in range(3):
            image = starter / 'images' / split / f'{cls}.jpg'
            frame = np.random.default_rng(s*10+cls).integers(0, 255, (24, 32, 3), dtype=np.uint8)
            cv2.imwrite(str(image), frame)
            (starter / 'labels' / split / f'{cls}.txt').write_text(f'{cls} 0.5 0.5 0.5 0.5\n')
            records.append(dict(image=image.relative_to(starter).as_posix(), source_group=f'{split}-{cls}',
                                origin='generated test data', usage_rights='test only', reviewed='upstream'))
    with (starter / 'sources.csv').open('w') as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS)
        writer.writeheader(); writer.writerows(records)
    (starter / 'dataset.yaml').write_text(yaml.safe_dump(dict(path='.', names=NAMES[:3], **{s:f'images/{s}' for s in SPLITS})))
    archive_path = tmp_path / 'source.zip'
    with zipfile.ZipFile(archive_path, 'w') as archive:
        archive.writestr('yolo_bbox/data.yaml', yaml.safe_dump(dict(names=list(MAPPING))))
        for s, split in enumerate(('train', 'valid', 'test')):
            for cls in range(12):
                frame = np.random.default_rng(100+s*20+cls).integers(0, 255, (24, 32, 3), dtype=np.uint8)
                archive.writestr(f'images/{split}/images/{s}_{cls}.jpg', cv2.imencode('.jpg', frame)[1].tobytes())
                archive.writestr(f'yolo_bbox/{split}/labels/{s}_{cls}.txt', f'{cls} 0.5 0.5 0.5 0.5\n')
    monkeypatch.setattr(module, 'SHA256', artifact_digest(archive_path))
    data = module.prepare(starter, archive_path, tmp_path / 'merged')
    report = audit(data, allow_upstream=True)
    assert report['valid'], report['errors']
    assert report['names'] == NAMES
    assert all(value['images'] == 15 for value in report['counts'].values())
    assert report['counts']['test']['boxes'] == dict(gun=1, knife=1, grenade=2, bomb=3, other_ordnance=8)
    assert not audit(data)['valid']
