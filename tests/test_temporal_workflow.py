import io
import json
import time
from pathlib import Path
import cv2
import numpy as np
from vmd.app import create_app
from vmd.storage import Store
from vmd.learning import Learner


def test_validated_candidate_activates_and_survives_restart(tmp_path, monkeypatch):
    import vmd.learning as module
    class Encoder:
        def __init__(self, *args): pass
        def clip(self, path):
            return np.full((16, 512), -1 if path.read_text().startswith('normal') else 1, np.float32)
    monkeypatch.setattr(module, 'Encoder', Encoder)
    store = Store(tmp_path)
    try:
        examples = []
        for group, count in [('a', 3), ('b', 3), ('c', 5)]:
            for label in ['normal', 'fight']:
                for index in range(count):
                    name = f'{label}-{group}-{index}.avi'
                    (store.raw_clips / name).write_text(name)
                    examples.append(dict(raw_clip=name, label=label, camera_id=group, weight=3, reviewed=True))
        monkeypatch.setattr(store, 'training_examples', lambda: examples)
        store.learner._train()
        state = store.learner.snapshot()
        assert state['state'] == 'ready', state
        assert state['active']
        assert state['evaluation']['event_recall'] == 1
        assert store.learner.metadata['training_sources'] == ['a', 'b']
        restored = Learner(store)
        try:
            assert restored.snapshot()['active']
            assert restored.metadata['labels'] == ['fight', 'normal']
        finally:
            restored.close()
        # Insufficient retraining must keep the existing active model untouched.
        active_bytes = store.learner.path.read_bytes()
        monkeypatch.setattr(store, 'training_examples', lambda: examples[:1])
        store.learner._train()
        assert store.learner.snapshot()['state'] == 'error'
        assert store.learner.path.read_bytes() == active_bytes
        assert store.learner.snapshot()['active']
    finally:
        store.close()


def test_docs_languages_have_identical_topics_and_local_routes(tmp_path):
    app = create_app(tmp_path)
    try:
        client = app.test_client()
        topics = None
        for language in ['en', 'hi', 'ta', 'ur', 'kn', 'te', 'bn', 'mr', 'gu', 'ml']:
            response = client.get(f'/static/docs/{language}.json')
            assert response.status_code == 200
            data = response.json
            keys = [section[0] for section in data['sections']]
            assert len(keys) == 6 and len(set(keys)) == 6
            if topics is None: topics = keys
            assert keys == topics
            assert all(len(section[2]) > 100 for section in data['sections'])
        engineering = client.get('/static/docs/engineering.json').json
        assert engineering['audience'] == 'engineering'
        assert len(engineering['sections']) == 11
        assert 'class-coverage' in str(engineering)
        page = client.get('/').text
        assert 'id="docs-audience"' in page
        assert 'value="ur"' in page and 'value="kn"' in page
        assert 'data-page="docs"' in page
        assert 'id="docs-language"' in page
        assert 'id="dataset-form"' in page
    finally:
        app.extensions['vmd_manager'].close()


def test_training_dataset_import_label_group_and_delete(tmp_path):
    path = tmp_path / 'fixture.avi'
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*'MJPG'), 5, (64, 48))
    for _ in range(5): writer.write(np.zeros((48, 64, 3), np.uint8))
    writer.release()
    app = create_app(tmp_path / 'data')
    headers = {'X-VMD-Client': 'dashboard'}
    client = app.test_client()
    try:
        assert client.post('/api/training/uploads', data={}).status_code == 403
        result = client.post('/api/training/uploads', data={'video': (io.BytesIO(path.read_bytes()), 'normal.avi'), 'label': 'normal', 'group': 'Gate A'}, headers=headers)
        assert result.status_code == 201
        rows = client.get('/api/training/uploads').json
        assert rows[0]['camera_id'] == 'dataset:gate a'
        store = app.extensions['vmd_manager'].store
        assert store.training_examples()[0]['reviewed']
        assert store.training_audit()['reviewed_clips'] == 1
        assert client.get('/api/training/uploads/' + result.json['id']).status_code == 200
        assert client.delete('/api/training/uploads/' + result.json['id'], headers=headers).status_code == 200
        assert store.training_examples() == []
        invalid = client.post('/api/training/uploads', data={'video': (io.BytesIO(b'not video'), 'broken.mp4'), 'label': 'fight', 'group': 'A'}, headers=headers)
        assert invalid.status_code == 400
        assert not list(store.raw_clips.iterdir())
    finally:
        app.extensions['vmd_manager'].close()
