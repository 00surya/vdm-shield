"""Short local throughput check on a recording (not an accuracy benchmark)."""
import argparse
import json
import importlib.util
import statistics
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cv2
from vmd.models.sources import SourceSettings
from vmd.engine import Engine
from vmd.storage import Store


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, help="Local video to benchmark; default is a repeated sample image")
    parser.add_argument("--seconds", type=float, default=12, help="Measurement duration after the first analyzed frame")
    parser.add_argument("--target-fps", type=int, default=20, help="Maximum pose/motion analysis FPS")
    args = parser.parse_args()
    if args.seconds <= 0:
        parser.error("--seconds must be positive")
    if args.target_fps <= 0:
        parser.error("--target-fps must be positive")
    image=Path(importlib.util.find_spec('ultralytics').origin).parent/'assets/bus.jpg'
    frame=cv2.resize(cv2.imread(str(image)),(384,512))
    measurements=[]
    with tempfile.TemporaryDirectory() as temporary:
        if args.source:
            path = args.source.resolve()
            if not path.is_file():
                parser.error("--source must be an existing local video")
        else:
            path=Path(temporary)/'sample.avi'
            writer=cv2.VideoWriter(str(path),cv2.VideoWriter_fourcc(*'MJPG'),20,(384,512))
            for _ in range(1200):writer.write(frame)
            writer.release()
        for depth in ('ZipDepth',):
            store=Store(Path(temporary)/depth)
            engine=Engine(store,'models')
            seen=set()
            samples=[]
            depth_frames=set()
            work_times=[]
            input_rates=[]
            first_pose=None
            first_depth=None
            started=time.monotonic()
            try:
                engine.start(SourceSettings(source=str(path),target_fps=args.target_fps))
                while time.monotonic()-started<25:
                    state=engine.snapshot()
                    assert state['status']!='error',state
                    if state['sequence'] and first_pose is None:first_pose=time.monotonic()
                    if state['sequence'] and state['sequence'] not in seen:
                        seen.add(state['sequence'])
                        if first_pose and time.monotonic()-first_pose>3:
                            samples.append(state['processed_fps'])
                            work_times.append(state['latency_ms'])
                            if state.get('capture_fps'):input_rates.append(state['capture_fps'])
                    meta=state.get('depth_meta',{})
                    assert meta.get('status')!='error',meta
                    if meta.get('sequence') is not None:
                        depth_frames.add(meta['sequence'])
                        if first_depth is None:first_depth=time.monotonic()
                    if state['status'] == 'finished' or (first_pose and time.monotonic()-first_pose>args.seconds):break
                    time.sleep(.03)
                assert samples and depth_frames,state
                measurements.append({'depth':depth,'target_fps':args.target_fps,'source_reported_fps':state.get('source_fps'),
                                     'median_input_fps':round(statistics.median(input_rates),2) if input_rates else None,
                                     'captured_frames':state.get('captured_frames'), 'processed_frames':state.get('processed_frames'),
                                     'skipped_frames':state.get('skipped_frames'),
                                     'median_pose_fps':round(statistics.median(samples),2),
                                     'median_frame_work_ms':round(statistics.median(work_times),1) if work_times else None,
                                     'depth_frames':len(depth_frames), 'depth_people':state.get('depth_people', []),
                                     'depth_model':meta.get('model'), 'first_pose_seconds':round(first_pose-started,2),
                                     'first_depth_seconds':round(first_depth-started,2) if first_depth else None})
            finally:
                engine.stop()
                store.close()
    print(json.dumps(measurements,indent=2))


if __name__=='__main__':
    main()
