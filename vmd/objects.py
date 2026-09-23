"""General scene objects; these detections never enter the threat alert gate."""
import hashlib
from pathlib import Path

WEIGHTS = 'yolo26s.pt'
MODEL_URL = 'https://github.com/ultralytics/assets/releases/download/v8.4.0/yolo26s.pt'
SHA256 = '646f8bc3fe0a656803d95c294f7852321748cb29d13466a1af8862e2db384a1b'

COCO_CLASSES = ('person', 'bicycle', 'car', 'motorcycle', 'airplane', 'bus', 'train', 'truck', 'boat', 'traffic light', 'fire hydrant', 'stop sign', 'parking meter', 'bench', 'bird', 'cat', 'dog', 'horse', 'sheep', 'cow', 'elephant', 'bear', 'zebra', 'giraffe', 'backpack', 'umbrella', 'handbag', 'tie', 'suitcase', 'frisbee', 'skis', 'snowboard', 'sports ball', 'kite', 'baseball bat', 'baseball glove', 'skateboard', 'surfboard', 'tennis racket', 'bottle', 'wine glass', 'cup', 'fork', 'knife', 'spoon', 'bowl', 'banana', 'apple', 'sandwich', 'orange', 'broccoli', 'carrot', 'hot dog', 'pizza', 'donut', 'cake', 'chair', 'couch', 'potted plant', 'bed', 'dining table', 'toilet', 'tv', 'laptop', 'mouse', 'remote', 'keyboard', 'cell phone', 'microwave', 'oven', 'toaster', 'sink', 'refrigerator', 'book', 'clock', 'vase', 'scissors', 'teddy bear', 'hair drier', 'toothbrush')


class ObjectModel:
    def __init__(self, model_dir, device='cpu'):
        path = Path(model_dir).resolve() / WEIGHTS
        if not path.is_file():
            raise RuntimeError('General object weights missing. Run scripts/download_object_model.py.')
        if not SHA256 or hashlib.sha256(path.read_bytes()).hexdigest() != SHA256:
            raise RuntimeError('General object weights checksum mismatch. Re-download the pinned model.')
        from ultralytics import YOLO
        self.model = YOLO(str(path), task='detect')
        self.device = device
        names = self.model.names
        labels = tuple(names[i] for i in range(len(names)))
        if labels != COCO_CLASSES:
            raise RuntimeError('Expected the exact 80 COCO classes in their original order.')

    def infer(self, frame):
        result = self.model.predict(frame, imgsz=640, conf=.4, device=self.device,
                                    max_det=50, verbose=False)[0]
        if result.boxes is None:
            return []
        return [{'label': str(result.names[int(cls)]), 'confidence': round(float(score), 3),
                 'box': [round(float(x), 1) for x in box], 'context_only': True}
                for box, score, cls in zip(result.boxes.xyxy.cpu().tolist(),
                                          result.boxes.conf.cpu().tolist(), result.boxes.cls.cpu().tolist())]
