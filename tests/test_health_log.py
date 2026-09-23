from vmd.health_log import HealthLog


def test_health_transitions_persist_without_duplicate_poll_entries(tmp_path):
    log = HealthLog(tmp_path)
    evidence = {'last_error': None, 'writer_alive': True, 'queued_jobs': 0, 'dropped_records': 0}
    camera = {'camera_id': 'cam-test', 'status': 'running', 'source_kind': 'camera',
              'depth_status': 'ready', 'threat_status': 'ready'}
    assert log.observe([camera], evidence)['online'] == 1
    count = len(log.read('all'))
    log.observe([camera], evidence)
    assert len(log.read('all')) == count
    camera['status'] = 'error'
    assert log.observe([camera], evidence)['not_online'] == 1
    assert 'error' in log.read('warning')[0]['message']
    assert len(HealthLog(tmp_path).read('all')) > count
