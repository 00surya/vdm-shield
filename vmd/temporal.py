"""Frozen ImageNet ResNet18 + trainable GRU. All inference stays on CPU."""
import hashlib
import json
from pathlib import Path
import cv2
import numpy as np

FRAMES = 16
ENCODER_FILE = 'resnet18-f37072fd.pth'
ENCODER_HASH = 'f37072fd47e89c5e827621c5baffa7500819f7896bbacec160b1a16c560e07ec'
CONFIG = {'architecture': 'ResNet18 + GRU', 'frames': 16, 'input_size': 224, 'features': 512,
          'hidden_size': 64, 'epochs': 30, 'batch_size': 8, 'learning_rate': .001,
          'weight_decay': .0001, 'dropout': .2, 'confidence_threshold': .65,
          'frozen_parameters': 11176512, 'seed': 42, 'device': 'cpu'}


def sample_indices(total):
    return np.linspace(0, total - 1, FRAMES, dtype=int) if total >= 2 else []


class Encoder:
    def __init__(self, model_dir):
        import torch
        from torchvision.models import resnet18, ResNet18_Weights
        path = Path(model_dir) / ENCODER_FILE
        if not path.is_file() or not hashlib.sha256(path.read_bytes()).hexdigest().startswith(ENCODER_HASH):
            raise RuntimeError('Install verified ResNet18 weights: python scripts/download_clip_model.py')
        torch.set_num_threads(1)
        self.model = resnet18(weights=None)
        self.model.load_state_dict(torch.load(path, map_location='cpu', weights_only=True))
        self.model.fc = torch.nn.Identity()
        self.model.eval().requires_grad_(False)
        self.transform = ResNet18_Weights.IMAGENET1K_V1.transforms()

    def frames(self, frames):
        import torch
        if len(frames) != FRAMES or any(frame is None for frame in frames):
            raise ValueError('Need 16 decodable sampled frames')
        # Apply the pretrained RGB resize/crop/normalization recipe consistently.
        batch = torch.stack([self.transform(torch.from_numpy(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)).permute(2, 0, 1)) for frame in frames])
        with torch.inference_mode():
            return torch.cat([self.model(part) for part in batch.split(4)]).numpy()

    def clip(self, path):
        capture = cv2.VideoCapture(str(path))
        try:
            count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
            if count < 2:
                raise ValueError('Clip is too short')
            frames = []
            for index in sample_indices(count):
                capture.set(cv2.CAP_PROP_POS_FRAMES, int(index))
                ok, frame = capture.read()
                if not ok:
                    raise ValueError('Clip cannot be decoded')
                frames.append(frame)
            return self.frames(frames)
        finally:
            capture.release()


def new_head(classes):
    import torch
    class Head(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.gru = torch.nn.GRU(512, 64, batch_first=True)
            self.dropout = torch.nn.Dropout(.2)
            self.classifier = torch.nn.Linear(64, classes)
        def forward(self, sequence):
            _, hidden = self.gru(sequence)
            return self.classifier(self.dropout(hidden[-1]))
    return Head()


def fit_head(features, targets, weights, classes, progress=None, cancelled=None):
    import torch
    torch.set_num_threads(1)
    torch.manual_seed(42)
    head = new_head(classes)
    x = torch.tensor(np.stack(features), dtype=torch.float32)
    y = torch.tensor(targets, dtype=torch.long)
    counts = torch.bincount(y, minlength=classes).clamp_min(1)
    balance = len(y) / (classes * counts.float())
    w = torch.tensor(weights, dtype=torch.float32) * balance[y]
    optimizer = torch.optim.AdamW(head.parameters(), lr=.001, weight_decay=.0001)
    generator = torch.Generator().manual_seed(42)
    for epoch in range(CONFIG['epochs']):
        if cancelled and cancelled.is_set():
            raise RuntimeError('Training cancelled during shutdown')
        head.train()
        for indices in torch.randperm(len(y), generator=generator).split(8):
            optimizer.zero_grad()
            losses = torch.nn.functional.cross_entropy(head(x[indices]), y[indices], reduction='none')
            loss = (losses * w[indices]).sum() / w[indices].sum()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(head.parameters(), 1.)
            optimizer.step()
        if progress:
            progress(epoch + 1)
    return head.eval()


def predict_features(head, labels, features):
    import torch
    with torch.inference_mode():
        probs = head(torch.tensor(np.stack(features), dtype=torch.float32)).softmax(-1).numpy()
    results = []
    for row in probs:
        index = int(row.argmax()); confidence = float(row[index])
        label = labels[index] if confidence >= CONFIG['confidence_threshold'] else 'normal'
        results.append({'label': label, 'confidence': round(confidence, 4), 'margin': round(confidence, 4)})
    return results


def metrics(truth, predicted):
    tp = sum(a != 'normal' and p != 'normal' for a, p in zip(truth, predicted))
    fp = sum(a == 'normal' and p != 'normal' for a, p in zip(truth, predicted))
    fn = sum(a != 'normal' and p == 'normal' for a, p in zip(truth, predicted))
    tn = sum(a == 'normal' and p == 'normal' for a, p in zip(truth, predicted))
    return {'state': 'measured', 'clips': len(truth),
            'event_precision': tp / (tp + fp) if tp + fp else 0.,
            'event_recall': tp / (tp + fn) if tp + fn else 0.,
            'binary_accuracy': (tp + tn) / len(truth),
            'type_accuracy': sum(a == p for a, p in zip(truth, predicted)) / len(truth),
            'confusion': {'true_event': tp, 'false_alarm': fp, 'missed_event': fn, 'true_normal': tn},
            'per_label': [{'label': label, 'clips': truth.count(label), 'correct': sum(a == label and p == label for a, p in zip(truth, predicted))} for label in sorted(set(truth))]}


def save_model(path, head, metadata):
    temporary = path.with_suffix('.tmp.npz')
    np.savez_compressed(temporary, metadata=np.asarray(json.dumps(metadata)),
                        **{key: value.detach().cpu().numpy() for key, value in head.state_dict().items()})
    temporary.replace(path)


def load_model(path):
    import torch
    with np.load(path, allow_pickle=False) as data:
        meta = json.loads(str(data['metadata'].item()))
        if meta['config'] != CONFIG:
            raise ValueError('Incompatible temporal model configuration; retrain')
        head = new_head(len(meta['labels']))
        head.load_state_dict({key: torch.from_numpy(data[key].copy()) for key in head.state_dict()})
    return head.eval(), meta
