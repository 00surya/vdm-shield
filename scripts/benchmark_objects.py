"""Compare local object detector latency on bundled imagery; not an accuracy test."""
import importlib.util
import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cv2
import torch
from vmd.threats import ThreatModel, ThreatWorker
from vmd.objects import ObjectModel


def main():
    cv2.setNumThreads(1)
    torch.set_num_threads(1)
    root = Path(__file__).resolve().parents[1] / 'models'
    frame = cv2.imread(str(Path(importlib.util.find_spec('ultralytics').origin).parent / 'assets/bus.jpg'))
    report = {}
    for name, model in [('weapons', ThreatModel(root)), ('scene_objects', ObjectModel(root))]:
        model.infer(frame)
        times = []
        for _ in range(10):
            start = time.perf_counter()
            results = model.infer(frame)
            times.append((time.perf_counter() - start) * 1000)
        report[name] = {'median_ms': round(statistics.median(times), 1),
                        'labels': sorted({item['label'] for item in results})}
    worker = ThreatWorker('cpu', root)
    try:
        worker.start()
        start = time.monotonic()
        while time.monotonic() - start < 30:
            state, sample, error = worker.poll()
            if error:
                raise RuntimeError(error)
            if sample:
                assert sample['object_status'] == 'ready', sample.get('object_error')
                assert any(item['label'] == 'bus' for item in sample['scene_objects'])
                report['combined_worker'] = {'latency_ms': sample['latency_ms'],
                                            'scene_labels': sorted({item['label'] for item in sample['scene_objects']})}
                break
            if state == 'ready':
                worker.submit(1, 0, frame)
            time.sleep(.05)
        else:
            raise RuntimeError('Combined worker timed out')
    finally:
        worker.stop()
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
