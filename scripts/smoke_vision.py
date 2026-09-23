"""Check real model plumbing on a bundled public sample, without opening a camera."""
from pathlib import Path
import cv2
import numpy as np
from vmd.vision import PoseModel, DepthModel
from vmd.spatial import person_depths

pose = PoseModel('models/yolo11n-pose.pt', 'cpu')
import ultralytics
sample = Path(ultralytics.__file__).parent / 'assets' / 'bus.jpg'
frame = cv2.imread(str(sample))
assert frame is not None, 'Bundled Ultralytics sample image missing'
frame = cv2.resize(frame, (384, 512))
people = pose.infer(frame)
assert people and all(len(person.keypoints) == 17 for person in people)
depth = DepthModel('cpu', 'models').infer(frame)
assert depth.shape == frame.shape[:2] and np.isfinite(depth).all()
readings = person_depths(depth, people)
assert readings and any(item['relative_z'] is not None for item in readings)
print(f'PASS: {len(people)} people with 17-keypoint poses; real ZipDepth depth {depth.shape}; relative Z: {readings}.')
print('This verifies inference plumbing, not fight-detection accuracy.')
