import time
from types import SimpleNamespace

import numpy as np

from vmd.engine import Engine
from vmd.heuristics import Person


def sample_and_cache():
    points = [(0, 0, 0)] * 17
    for index, x, y in [(5, 20, 20), (6, 35, 20), (11, 20, 40), (12, 35, 40)]:
        points[index] = (x, y, .9)
    person = Person(7, (10, 10, 45, 55), points)
    depth = np.full((64, 96), .2, np.float32)
    depth[:, :48] = .8
    cache = {12: dict(source_time=1.2, shape=depth.shape, people=[person])}
    sample = dict(sequence=12, source_time=1.2, submitted_at=time.time(), latency_ms=10,
                  identity=dict(session_id='sample-session'), depth=depth)
    return sample, cache


def test_z_uses_sampled_pose_even_when_newer_track_coordinates_exist():
    sample, cache = sample_and_cache()
    cache[13] = dict(source_time=1.3, shape=(64, 96), people=[])
    state = Engine.depth_sample_state(sample, cache)
    assert state['depth_people'] == [dict(track_id=7, relative_z=.2)]
    assert state['depth_sequence'] == 12 and state['depth_pair_sequence'] == 12
    assert state['depth_identity']['session_id'] == 'sample-session'


def test_missing_mismatched_or_evicted_pose_clears_z_instead_of_reusing_tracks():
    sample, cache = sample_and_cache()
    for invalid in [{}, {12: dict(cache[12], source_time=1.3)}, {12: dict(cache[12], shape=(100, 100))}]:
        state = Engine.depth_sample_state(sample, invalid)
        assert state['depth_people'] == state['depth_pairs'] == []
        assert state['depth_z_status'] == 'no_pose'


def test_stale_and_failed_samples_hide_z_but_finished_recording_retains_it():
    sample, cache = sample_and_cache()
    engine = Engine(SimpleNamespace(error=None, dropped=0))
    engine.state.update(Engine.depth_sample_state(sample, cache), status='running', depth_status='ready', depth_available=True)
    assert engine.snapshot()['depth_people']
    engine.state['depth_submitted_at'] = time.time()-20
    assert engine.snapshot()['depth_people'] == []
    engine.state['status'] = 'finished'
    assert engine.snapshot()['depth_people']
    engine.state['depth_status'] = 'error'
    assert engine.snapshot()['depth_people'] == []
