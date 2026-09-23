"""Background training and bounded, source-isolated temporal inference."""
import hashlib
import json
import os
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import cv2
import numpy as np
from .temporal import CONFIG, Encoder, sample_indices, fit_head, predict_features, metrics, save_model, load_model


def holdout_split(examples):
    """Keep whole sources out of training; never split frames from one clip."""
    reviewed = [i for i, e in enumerate(examples) if e['reviewed']]
    sources = sorted({examples[i]['camera_id'] for i in reviewed})
    if len(reviewed) < 20 or len(sources) < 3:
        return None
    # Deterministic choice based on source/label counts, never on model scores.
    for source in reversed(sources):
        test = [i for i in reviewed if examples[i]['camera_id'] == source]
        train = [i for i, e in enumerate(examples) if e['camera_id'] != source]
        normal = sum(examples[i]['label'] == 'normal' for i in test)
        counts = {label: sum(examples[i]['label'] == label for i in train) for label in {e['label'] for e in examples}}
        if normal >= 5 and len(test) - normal >= 5 and counts.get('normal', 0) >= 3 and any(k != 'normal' and v >= 2 for k, v in counts.items()):
            return train, test
    return None


def promotion_gate(evaluation, baseline=None):
    if evaluation.get('state') != 'measured':
        return False, 'Candidate saved. Need 20 reviewed clips across 3 sources, with 5 normal and 5 event clips on a held-out source.'
    if evaluation['event_recall'] < .8 or evaluation['event_precision'] < .6:
        return False, 'Candidate saved, not activated: held-out event recall must reach 80% and precision 60%.'
    if baseline and (evaluation['event_recall'] < baseline['event_recall'] or evaluation['event_precision'] < baseline['event_precision']):
        return False, 'Candidate saved, not activated: recall or precision fell below the active model on the same held-out clips.'
    return True, 'Temporal model activated after held-out validation. Existing detectors remain active.'


def class_coverage_gate(labels, evaluation, active_labels=()):
    """Do not silently forget deployed labels or promote unvalidated event types."""
    missing = set(active_labels) - set(labels)
    if missing:
        return False, 'Candidate retained: missing active classes: ' + ', '.join(sorted(missing))
    per_label = {item['label']: item for item in evaluation.get('per_label', [])}
    for label in labels:
        row = per_label.get(label, {})
        support = row.get('clips', 0)
        if support < 5:
            return False, f'Candidate retained: need at least 5 held-out reviewed clips for {label}.'
        if row.get('correct', 0) / support < .8:
            return False, f'Candidate retained: held-out recall for {label} is below 80%.'
    if baseline_missing := set(per_label) - set(labels):
        return False, 'Candidate retained: validation includes unsupported classes: ' + ', '.join(sorted(baseline_missing))
    return True, 'All candidate classes passed coverage checks.'


class Learner:
    def __init__(self, store):
        self.store = store
        self.path = Path(store.directory) / 'temporal_model.npz'
        self.candidate = self.path.with_name('temporal_candidate.npz')
        self.report = self.path.with_name('temporal_training.json')
        self.model_dir = Path(os.getenv('VMD_MODEL_DIR', 'models'))
        self.lock = threading.RLock()
        self.encoder_lock = threading.Lock()
        self.encoder = None
        self.model = None
        self.metadata = None
        self.closed = threading.Event()
        self.trainer = None
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='clip-inference')
        self.pending = {}
        self.status = {'state': 'idle', 'message': 'Collect labeled clips to train ResNet18 + GRU.', 'trained_at': None,
                       'counts': {}, 'config': CONFIG, 'active': False,
                       'evaluation': {'state': 'unavailable', 'reason': 'No temporal candidate has been evaluated.'}}
        if self.report.is_file():
            try:
                self.status.update(json.loads(self.report.read_text()))
            except (OSError, ValueError):
                pass
        if self.path.is_file():
            try:
                self.model, self.metadata = load_model(self.path)
            except Exception:
                self.status.update(state='error', message='Saved temporal model could not load. Retrain the clip model.')
        self.status['active'] = self.model is not None
        if self.status['state'] == 'training':
            self.status.update(state='idle', message='Previous training was interrupted; retry training.')
        if (Path(store.directory) / 'learned_model.npz').exists() and self.model is None:
            self.status['message'] = 'Legacy centroid model is inactive. Train the new temporal model.'

    def snapshot(self):
        with self.lock:
            return {**self.status, 'active': self.model is not None,
                    'active_trained_at': self.metadata.get('trained_at') if self.metadata else None,
                    'active_evaluation': self.metadata.get('evaluation') if self.metadata else None}

    def _encoder(self):
        if self.encoder is None:
            self.encoder = Encoder(self.model_dir)
        return self.encoder

    def start_training(self):
        with self.lock:
            if self.status['state'] == 'training' or self.closed.is_set():
                return False
            self.status.update(state='training', message='Preparing raw clips and cached ResNet features…', epoch=0)
            self.trainer = threading.Thread(target=self._train, daemon=True, name='temporal-trainer')
            self.trainer.start()
            return True

    def _train(self):
        try:
            examples = self.store.training_examples()
            cache = Path(self.store.directory) / 'feature_cache'
            cache.mkdir(exist_ok=True)
            entries, features, seen, conflicts = [], [], {}, set()
            for example in examples:
                path = self.store.raw_clips / example['raw_clip']
                if not path.is_file():
                    continue
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                if digest in seen:
                    if seen[digest]['label'] != example['label']:
                        conflicts.add(digest)
                    elif example['reviewed'] and not seen[digest]['reviewed']:
                        seen[digest].update(example)
                    continue
                seen[digest] = {**example, 'digest': digest}
            for example in seen.values():
                if self.closed.is_set():
                    return
                if example['digest'] in conflicts:
                    continue
                path = self.store.raw_clips / example['raw_clip']
                cached = cache / f"resnet18-v1-16-{example['digest']}.npy"
                try:
                    if cached.is_file():
                        vector = np.load(cached, allow_pickle=False)
                    else:
                        with self.encoder_lock:
                            vector = self._encoder().clip(path)
                        np.save(cached, vector, allow_pickle=False)
                    if vector.shape != (16, 512) or not np.isfinite(vector).all():
                        raise ValueError('Invalid features')
                except (ValueError, cv2.error):
                    continue
                entries.append(example); features.append(vector)
                with self.lock:
                    self.status['message'] = f"Prepared {len(entries)} distinct clips. Training the temporal head next…"
            counts = {label: sum(e['label'] == label for e in entries) for label in sorted({e['label'] for e in entries})}
            if counts.get('normal', 0) < 3 or not any(k != 'normal' and v >= 2 for k, v in counts.items()):
                raise ValueError('Need 3 distinct normal clips and 2 distinct clips of one event type. Exact duplicates, conflicting duplicates and unreadable clips are excluded.')
            split = holdout_split(entries)
            train, test = split if split else (list(range(len(entries))), [])
            labels = sorted({entries[i]['label'] for i in train if sum(entries[j]['label'] == entries[i]['label'] for j in train) >= (3 if entries[i]['label'] == 'normal' else 2)})
            train = [i for i in train if entries[i]['label'] in labels]
            def progress(epoch):
                with self.lock:
                    self.status.update(epoch=epoch, message=f'Training GRU: epoch {epoch}/{CONFIG["epochs"]}')
            head = fit_head([features[i] for i in train], [labels.index(entries[i]['label']) for i in train],
                            [entries[i].get('weight', 1) for i in train], len(labels), progress, self.closed)
            evaluation = {'state': 'unavailable', 'reason': 'Need 20 reviewed clips across 3 sources and a held-out source with 5 normal and 5 event clips.'}
            baseline = None
            comparable = True
            if test:
                truth = [entries[i]['label'] for i in test]
                predictions = predict_features(head, labels, [features[i] for i in test])
                evaluation = metrics(truth, [p['label'] for p in predictions])
                evaluation.update(sources=1, eligible_clips=sum(e['reviewed'] for e in entries),
                                  skipped_clips=sum(e['reviewed'] for e in entries)-len(test), method='source_holdout')
                if self.model:
                    # Compare only if none of these test clips/sources trained the active model.
                    clean = not set(self.metadata['training_sources']).intersection({entries[i]['camera_id'] for i in test}) and not set(self.metadata['training_digests']).intersection({entries[i]['digest'] for i in test})
                    if clean:
                        prior = predict_features(self.model, self.metadata['labels'], [features[i] for i in test])
                        baseline = metrics(truth, [p['label'] for p in prior])
                    else:
                        comparable = False
            promote, message = promotion_gate(evaluation, baseline)
            coverage_ok, coverage_message = class_coverage_gate(
                labels, evaluation, self.metadata.get('labels', []) if self.model else [])
            if not coverage_ok:
                promote = False
                message = coverage_message
            if not comparable:
                promote = False
                message = 'Candidate saved. Active model saw the selected validation source; collect an independent source before replacement.'
            meta = {'labels': labels, 'config': CONFIG, 'trained_at': time.time(), 'evaluation': evaluation,
                    'training_sources': sorted({entries[i]['camera_id'] for i in train}),
                    'training_digests': [entries[i]['digest'] for i in train],
                    'trainable_parameters': sum(p.numel() for p in head.parameters()), 'counts': counts}
            save_model(self.candidate, head, meta)
            if promote:
                if self.path.exists():
                    shutil.copy2(self.path, self.path.with_name('temporal_previous.npz'))
                temporary = self.path.with_suffix('.new.npz')
                shutil.copy2(self.candidate, temporary); temporary.replace(self.path)
                with self.lock:
                    self.model, self.metadata = head, meta
            with self.lock:
                self.status.update(state='ready' if promote else 'candidate', message=message, counts=counts,
                                   trained_at=meta['trained_at'], evaluation=evaluation, config=CONFIG,
                                   trainable_parameters=meta['trainable_parameters'], distinct_clips=len(entries),
                                   conflicting_duplicates=len(conflicts), active=self.model is not None)
                self.report.write_text(json.dumps(self.status, indent=2))
        except Exception as exc:
            with self.lock:
                self.status.update(state='error', message=str(exc) if isinstance(exc, (ValueError, RuntimeError)) else f'Training failed ({type(exc).__name__}); existing model retained.')

    def _infer(self, frames, model, labels):
        sampled = [cv2.imdecode(np.frombuffer(frames[int(i)][1], np.uint8), cv2.IMREAD_COLOR) for i in sample_indices(len(frames))]
        with self.encoder_lock:
            features = self._encoder().frames(sampled)
        result = predict_features(model, labels, [features])[0]
        return result if result['label'] != 'normal' else None

    def predict(self, frames, source_id='default'):
        """Never block capture on neural inference; stale and cross-source results cannot alert."""
        with self.lock:
            if self.model is None or self.closed.is_set() or len(frames) < 2:
                return None
            prediction = None
            job = self.pending.get(source_id)
            if job and job[0].done():
                future, submitted, source_time, version = self.pending.pop(source_id)
                try:
                    result = future.result()
                    if time.monotonic() - submitted <= 3 and 0 <= frames[-1][0] - source_time <= 3 and version is self.model:
                        prediction = {**result, "source_seconds": source_time} if result else None
                except Exception as exc:
                    self.status['inference_error'] = f'Clip inference unavailable ({type(exc).__name__})'
            if source_id not in self.pending and len(self.pending) < 4:
                future = self.executor.submit(self._infer, list(frames), self.model, self.metadata['labels'])
                self.pending[source_id] = (future, time.monotonic(), frames[-1][0], self.model)
            return prediction

    def forget_source(self, source_id):
        with self.lock:
            job = self.pending.pop(source_id, None)
            if job:
                job[0].cancel()

    def close(self):
        self.closed.set()
        self.executor.shutdown(wait=True, cancel_futures=True)
        if self.trainer and self.trainer.is_alive():
            self.trainer.join(timeout=30)
