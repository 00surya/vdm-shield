import queue
import threading
import time
from collections import deque
from types import SimpleNamespace

import numpy as np

from vmd.capture import Capture
from vmd.depth_worker import DepthWorker


def test_worker_preserves_original_frame_identity_after_delay():
    worker = DepthWorker.__new__(DepthWorker)
    worker.requests = queue.Queue(maxsize=1)
    worker.responses = queue.Queue(maxsize=1)
    worker.process = SimpleNamespace(exitcode=None)
    worker.closed = False
    worker.status = 'ready'
    worker.error = worker.result = None
    worker.interval = .5
    worker.last_submit = float('-inf')
    worker.priority_sequence = worker.priority_at = None
    identity = {'session_id': 'session-a', 'frame_id': 78, 'source_seconds': 2.6,
                'captured_monotonic': time.monotonic() - .5}
    assert worker.submit(78, 2.6, np.zeros((4, 4, 3), np.uint8), identity=identity)
    worker.responses.put({'status': 'ready', 'sequence': 78, 'source_time': 2.6,
                          'jpeg': b'frame78', 'latency_ms': 200})
    _, sample, _ = worker.poll()
    assert sample['identity']['frame_id'] == 78
    assert sample['identity']['session_id'] == 'session-a'
    assert sample['capture_to_result_ms'] >= 500
    assert worker.metrics()['completed_samples'] == 1


def test_capture_evidence_window_uses_event_time_not_detection_time():
    capture = Capture.__new__(Capture)
    capture.lock = threading.Lock()
    capture.evidence = deque([(0, b'pre'), (2.6, b'event'), (5, b'post'), (9, b'late')])
    assert capture.evidence_between(2.6) == [(0, b'pre'), (2.6, b'event'), (5, b'post')]
