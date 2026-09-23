import numpy as np

from vmd.heuristics import Person
from vmd.spatial import compare_people, torso_depth, person_depths


def person(track_id, box, xs, confidence=.9):
    points = [(0, 0, 0)] * 17
    for index, x, y in zip((5, 6, 11, 12), (*xs[:2], *xs[2:]), (55, 55, 105, 105)):
        points[index] = (x, y, confidence)
    return Person(track_id, box, points)


def test_overlapping_people_have_same_frame_relative_order():
    depth = np.full((200, 140), .2, dtype=np.float32)
    depth[:, :65] = .8
    left = person(1, (20, 20, 80, 180), (35, 45, 37, 47))
    right = person(2, (50, 20, 110, 180), (85, 95, 87, 97))
    pair = compare_people(depth, [left, right])[0]
    assert pair['overlap']
    assert pair['status'] == 'separated'
    assert pair['nearer_track'] == 1
    assert pair['relative_gap'] == .6


def test_small_depth_gap_is_uncertain_and_bad_pose_is_uncertain():
    depth = np.full((200, 140), .5, dtype=np.float32)
    left = person(1, (20, 20, 80, 180), (35, 45, 37, 47))
    right = person(2, (50, 20, 110, 180), (85, 95, 87, 97))
    assert compare_people(depth, [left, right])[0]['status'] == 'uncertain'
    right.keypoints[5] = (85, 55, .1)
    right.keypoints[6] = (95, 55, .1)
    assert torso_depth(depth, right) is None
    assert compare_people(depth, [left, right])[0]['status'] == 'uncertain'


def test_distant_people_are_not_compared():
    depth = np.full((200, 700), .5, dtype=np.float32)
    left = person(1, (20, 20, 80, 180), (35, 45, 37, 47))
    right = person(2, (500, 20, 560, 180), (515, 525, 517, 527))
    assert compare_people(depth, [left, right]) == []


def test_relative_z_increases_away_from_camera_and_works_for_one_person():
    depth = np.full((200, 140), .2, dtype=np.float32)
    depth[:, :65] = .8
    near = person(1, (20, 20, 80, 180), (35, 45, 37, 47))
    far = person(2, (50, 20, 110, 180), (85, 95, 87, 97))
    assert person_depths(depth, [near, far]) == [dict(track_id=1, relative_z=.2), dict(track_id=2, relative_z=.8)]
    assert person_depths(depth, [near]) == [dict(track_id=1, relative_z=.2)]


def test_flat_maps_unreliable_poses_and_untracked_people_have_no_numeric_z():
    tracked = person(1, (20, 20, 80, 180), (35, 45, 37, 47))
    depth = np.full((200, 140), .5, dtype=np.float32)
    assert person_depths(depth, [tracked])[0]['relative_z'] is None
    depth[:, :65] = .8
    tracked.pose_reliable = False
    assert person_depths(depth, [tracked])[0]['relative_z'] is None
    tracked.track_id = -1
    assert person_depths(depth, [tracked]) == []
