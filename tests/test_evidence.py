from vmd.evidence import EvidenceRecorder


def record(incident_id, at, count=1):
    return {"id": incident_id, "score": .7, "reasons": [f"signal {count}"],
            "signals": {"source_seconds": at}, "event_count": count}


def test_event_keeps_pre_and_post_frames_and_groups_repeated_signals():
    recorder = EvidenceRecorder(post_seconds=4)
    recorder.begin(record("fight", 10), [(8, b"pre"), (10, b"trigger")],
                   [(8, b"raw-pre"), (10, b"raw-trigger")])
    recorder.append(11, b"after", b"raw-after")
    assert recorder.revise(record("fight", 12, count=2))
    recorder.append(13, b"later", b"raw-later")
    assert recorder.ready_ids(14) == []
    assert recorder.ready_ids(16) == ["fight"]
    updated, frames, raw = recorder.payload("fight")
    assert updated["event_count"] == 2
    assert updated["reasons"] == ["signal 1", "signal 2"]
    assert len(updated["signals"]["grouped_signals"]) == 2
    assert [time for time, _ in frames] == [8, 10, 11, 13]
    assert [time for time, _ in raw] == [8, 10, 11, 13]


def test_short_video_finishes_evidence_at_end_of_file():
    recorder = EvidenceRecorder(post_seconds=4)
    recorder.begin(record("knife", 1), [(0, b"pre"), (1, b"trigger")], [])
    recorder.append(1.5, b"post", b"raw")
    assert recorder.ready_ids(1.5) == []
    assert recorder.ready_ids(1.5, final=True) == ["knife"]
    assert recorder.payload("knife")[1][-1][0] == 1.5
