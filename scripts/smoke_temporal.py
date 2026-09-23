"""Real encoder/head smoke check on bundled imagery; not an accuracy evaluation."""
import importlib.util
import json
import sys
import tempfile
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cv2
import numpy as np
from vmd.temporal import Encoder, fit_head, predict_features, save_model, load_model, CONFIG


def main():
    root = Path(__file__).resolve().parents[1]
    encoder = Encoder(root / 'models')
    bus = cv2.imread(str(Path(importlib.util.find_spec('ultralytics').origin).parent / 'assets/bus.jpg'))
    blank = np.zeros_like(bus)
    start = time.monotonic()
    normal = encoder.frames([blank] * 16)
    event = encoder.frames([bus] * 16)
    elapsed = time.monotonic() - start
    assert normal.shape == event.shape == (16, 512)
    assert all(not parameter.requires_grad for parameter in encoder.model.parameters())
    head = fit_head([normal] * 3 + [event] * 2, [0, 0, 0, 1, 1], [3] * 5, 2)
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / 'candidate.npz'
        save_model(path, head, {'labels': ['normal', 'fixture_event'], 'config': CONFIG})
        restored, _ = load_model(path)
        results = predict_features(restored, ['normal', 'fixture_event'], [normal, event])
    assert [result['label'] for result in results] == ['normal', 'fixture_event'], results
    print(json.dumps({'encoder_seconds_per_16_frame_clip': round(elapsed / 2, 3),
                      'frozen_parameters': sum(p.numel() for p in encoder.model.parameters()),
                      'trainable_parameters_two_classes': sum(p.numel() for p in head.parameters()),
                      'synthetic_predictions': results, 'accuracy_evaluation': False}, indent=2))


if __name__ == '__main__':
    main()
