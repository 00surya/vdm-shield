import json
from vmd.app import create_app

HEADERS = {'X-VMD-Client': 'dashboard'}


def test_review_label_transitions_are_consistent_and_persist(tmp_path):
    app = create_app(tmp_path)
    store = app.extensions['vmd_manager'].store
    client = app.test_client()
    try:
        with store.connect() as conn:
            conn.execute("INSERT INTO incidents (id,created,mode,score,reasons,signals,event_type,raw_clip) VALUES ('event',1,'live',.8,'[]',?,'fight','fixture.avi')", (json.dumps({'model_generated': True}),))
        def saved():
            return store.incidents()[0]
        assert not store.training_examples()
        response = client.post('/api/incidents/event/label', json={'label': 'possible_fall'}, headers=HEADERS)
        assert response.json == {'train_label': 'possible_fall', 'review': 'confirmed'}
        assert saved()['review'] == 'confirmed'
        assert store.training_examples()[0]['label'] == 'possible_fall'
        client.post('/api/incidents/event/review', json={'decision': 'confirmed'}, headers=HEADERS)
        assert saved()['train_label'] == 'possible_fall'  # Confirm preserves correction.
        client.post('/api/incidents/event/review', json={'decision': 'false_positive'}, headers=HEADERS)
        assert saved()['train_label'] == 'normal'
        client.post('/api/incidents/event/label', json={'label': 'knife_detected'}, headers=HEADERS)
        assert saved()['review'] == 'confirmed'
        assert saved()['train_label'] == 'knife_detected'
        client.post('/api/incidents/event/label', json={'label': 'normal'}, headers=HEADERS)
        assert saved()['review'] == 'false_positive'
        client.post('/api/incidents/event/review', json={'decision': 'confirmed'}, headers=HEADERS)
        assert saved()['train_label'] is None
        assert store.training_examples()[0]['label'] == 'fight'
        client.post('/api/incidents/event/review', json={'decision': 'unreviewed'}, headers=HEADERS)
        assert saved()['review'] == 'unreviewed' and saved()['train_label'] is None
        assert not store.training_examples()
        assert client.post('/api/incidents/event/label', json={'label': 'invalid'}, headers=HEADERS).status_code == 400
        assert client.post('/api/incidents/missing/label', json={'label': 'fight'}, headers=HEADERS).status_code == 404
    finally:
        app.extensions['vmd_manager'].close()
