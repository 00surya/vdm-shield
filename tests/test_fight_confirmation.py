from dataclasses import replace

import numpy as np
import pytest

from vmd.behavior import BehaviorHeuristic, EVENT_LABELS
from vmd.heuristics import Person, Rules
from vmd.spatial import fight_depth_evidence


def interacting_people():
    people = []
    for track, x in ((1, 180), (2, 300)):
        points = [(x, 80, .99)] * 17
        points[5:7] = [(x-25, 110, .99), (x+25, 110, .99)]
        points[11:13] = [(x-20, 200, .99), (x+20, 200, .99)]
        points[9:11] = [(250, 155, .99), (x-30, 155, .99)]
        points[15:17] = [(x-25, 290, .99), (x+25, 290, .99)]
        people.append(Person(track, (x-80, 50, x+80, 300), points,
                             body_scale=250, local_flow=.1,
                             limb_speeds={9: 1.4, 10: 0, 15: 0, 16: 0},
                             limb_motion={9: .8}))
    return people


def depth_map(separated=False):
    depth = np.full((360, 440), .05, dtype=np.float32)
    depth[50:300, 100:260] = .3 if separated else .6
    depth[50:300, 220:380] = .85 if separated else .6
    return depth


def step(behavior, n, people=None, depth=True, camera=0, timestamp=None):
    timestamp = round(n * .1, 4) if timestamp is None else timestamp
    people = interacting_people() if people is None else people
    if depth and n % 5 == 0:
        behavior.confirmation.observe_depth(timestamp, fight_depth_evidence(depth_map(), people), timestamp)
    return behavior.update(people, timestamp, .1, camera)


def test_five_seconds_of_motion_and_distinct_depth_samples_before_one_alert():
    behavior = BehaviorHeuristic(Rules(hold_seconds=.1))  # Old settings cannot bypass the minimum.
    events, pending = [], []
    for n in range(200):
        result = step(behavior, n)
        if n < 55:
            assert not result.events and result.state != 'possible_fight'
        if result.state == 'checking_interaction':
            pending.append(result.signals['pending_pairs'])
        events += result.events
    assert pending
    assert len(events) == 1
    assert events[0].pair == (1, 2)
    assert events[0].signals['confirmation_seconds'] >= 5
    assert events[0].signals['depth_samples'] >= 3
    assert events[0].signals['motion_supported_seconds'] >= 5
    assert result.state == 'possible_fight'
    assert EVENT_LABELS['fight'] == 'POSSIBLE FIGHT / REVIEW'


@pytest.mark.parametrize('case', ['one_person', 'same_id', 'untracked', 'nonoverlap',
                                 'bad_pose', 'one_still', 'wrist_only', 'no_depth', 'short_wave'])
def test_missing_gate_never_creates_a_fight(case):
    behavior = BehaviorHeuristic()
    for n in range(100):
        people = interacting_people()
        if case == 'one_person':
            people = people[:1]
        elif case == 'same_id':
            people[1].track_id = 1
        elif case == 'untracked':
            people[1].track_id = -1
        elif case == 'nonoverlap':
            people[1].box = (270, 50, 430, 300)
        elif case == 'bad_pose':
            people[1].pose_reliable = False
        elif case == 'one_still':
            people[1].local_flow = 0
        elif case == 'wrist_only':
            for person in people:
                person.local_flow = .002
        elif case == 'short_wave' and n >= 35:
            for person in people:
                person.local_flow = 0
                person.limb_speeds = {}
        result = step(behavior, n, people, depth=case != 'no_depth')
        assert not result.events and result.state != 'possible_fight'
        assert behavior.recent_fights == {}


@pytest.mark.parametrize('break_kind', ['lost', 'new_id', 'nonoverlap', 'camera', 'motion', 'gap'])
def test_tracking_or_evidence_break_restarts_confirmation(break_kind):
    behavior = BehaviorHeuristic()
    events = []
    for n in range(110):
        people = interacting_people()
        timestamp = round(n*.1, 4)
        camera = 0
        if break_kind == 'new_id' and n >= 30:
            people[1].track_id = 3
        if n == 30:
            if break_kind == 'lost':
                people = people[:1]
            elif break_kind == 'nonoverlap':
                people[1].box = (270, 50, 430, 300)
            elif break_kind == 'camera':
                camera = .2
        if break_kind == 'motion' and 30 <= n <= 34:
            for person in people:
                person.local_flow = 0
        if break_kind == 'gap' and n >= 30:
            timestamp += 1
        result = step(behavior, n, people, camera=camera, timestamp=timestamp)
        if n < 85:
            assert not result.events
        events += result.events
    assert len(events) == 1


@pytest.mark.parametrize('depth_kind', ['missing', 'flat', 'separated', 'repeated', 'future', 'unknown'])
def test_unusable_depth_or_repolling_one_sample_cannot_confirm(depth_kind):
    behavior = BehaviorHeuristic()
    for n in range(100):
        timestamp = n*.1
        people = interacting_people()
        if n % 5 == 0:
            readings = fight_depth_evidence(depth_map(depth_kind == 'separated'), people)
            if depth_kind == 'flat':
                readings = fight_depth_evidence(np.full((360, 440), .5), people)
            elif depth_kind == 'missing':
                readings = []
            elif depth_kind == 'unknown':
                readings = [dict(item, status='uncertain') for item in readings]
            source_time = .5 if depth_kind == 'repeated' else timestamp + 1 if depth_kind == 'future' else timestamp
            behavior.confirmation.observe_depth(source_time, readings, timestamp)
        result = step(behavior, n, people, depth=False)
        assert not result.events and result.state != 'possible_fight'


@pytest.mark.parametrize('status', ['separated', 'uncertain', 'unavailable', 'stale'])
def test_depth_failure_restarts_evidence_instead_of_resuming_old_timer(status):
    behavior = BehaviorHeuristic()
    events = []
    for n in range(140):
        timestamp = n*.1
        if n == 35:
            if status == 'unavailable':
                behavior.confirmation.depth_unavailable()
            elif status != 'stale':
                behavior.confirmation.observe_depth(timestamp, [{'tracks': [1, 2], 'status': status}], timestamp)
        # Stop samples long enough to exceed the age limit for the stale case.
        depth = not (35 <= n < (65 if status == 'stale' else 40))
        result = step(behavior, n, depth=depth)
        if n < (115 if status == 'stale' else 90):
            assert not result.events
        events += result.events
    assert len(events) == 1


def test_short_pauses_retain_pair_but_do_not_count_as_supported_time():
    behavior = BehaviorHeuristic()
    events = []
    for n in range(140):
        people = interacting_people()
        if n % 10 == 8:
            for person in people:
                person.local_flow = 0
        result = step(behavior, n, people)
        if n < 65:
            assert not result.events
        events += result.events
    assert len(events) == 1
    assert events[0].signals['motion_supported_seconds'] >= 5


def test_simultaneous_pairs_have_independent_confirmation_windows():
    behavior = BehaviorHeuristic()
    events = []
    for n in range(120):
        people = interacting_people()
        if n >= 30:
            people += [replace(person, track_id=person.track_id+2,
                               box=tuple(value+600 if i%2 == 0 else value for i, value in enumerate(person.box)),
                               keypoints=[(x+600, y, c) for x, y, c in person.keypoints])
                       for person in interacting_people()]
        if n % 5 == 0:
            observations = [{'tracks': [1, 2], 'status': 'compatible'}]
            if n >= 30:
                observations += [{'tracks': [3, 4], 'status': 'compatible'}]
            behavior.confirmation.observe_depth(n*.1, observations, n*.1)
        result = step(behavior, n, people, depth=False)
        for event in result.events:
            events.append((n*.1, event.pair))
    assert [pair for _, pair in events] == [(1, 2), (3, 4)]
    assert events[1][0] >= 8


def test_delayed_depth_before_reacquisition_cannot_attach_to_reused_track_ids():
    behavior = BehaviorHeuristic()
    for n in range(30):
        step(behavior, n)
    step(behavior, 30, people=[])
    step(behavior, 31, depth=False)
    behavior.confirmation.observe_depth(2.9, [{'tracks': [1, 2], 'status': 'compatible'}], 3.2)
    assert behavior.confirmation.pending[(1, 2)].depth_last is None


def test_same_frame_depth_quality_distinguishes_compatibility_from_uncertainty():
    people = interacting_people()
    assert fight_depth_evidence(depth_map(), people)[0]['status'] == 'compatible'
    assert fight_depth_evidence(depth_map(True), people)[0]['status'] == 'separated'
    people[0].keypoints[5] = (155, 110, .1)
    people[0].keypoints[6] = (205, 110, .1)
    assert fight_depth_evidence(depth_map(), people)[0]['status'] == 'uncertain'
