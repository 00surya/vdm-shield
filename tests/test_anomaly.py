from vmd.anomaly import MotionOutlier


def test_persistent_person_motion_outlier_is_low_priority():
    detector = MotionOutlier()
    for index in range(61):
        assert detector.update(index / 10, .01, 0, 1) is None
    events = [detector.update(index / 10, .12, 0, 1) for index in range(61, 81)]
    alerts = [event for event in events if event]
    assert len(alerts) == 1
    assert alerts[0]["signals"]["priority"] == "low"
    assert alerts[0]["signals"]["detector"] == "motion_outlier"


def test_camera_movement_and_named_alert_suppress_motion_outlier():
    detector = MotionOutlier()
    for index in range(61):
        detector.update(index / 10, .01, 0, 1)
    for index in range(61, 81):
        assert detector.update(index / 10, .12, .2, 1) is None
    for index in range(81, 101):
        assert detector.update(index / 10, .12, 0, 1, named_active=True) is None
