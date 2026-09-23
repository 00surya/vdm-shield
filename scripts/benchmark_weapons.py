"""Measure candidate detector latency on one image; never an accuracy benchmark."""
import argparse
import importlib.util
import json
from pathlib import Path
import platform
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.train_weapons import configure_runtime


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--weights', type=Path, nargs='+', required=True)
    parser.add_argument('--image', type=Path)
    parser.add_argument('--imgsz', type=int, nargs='+', default=[480, 640])
    parser.add_argument('--runs', type=int, default=20)
    parser.add_argument('--threads', type=int, default=1)
    parser.add_argument('--device', default='cpu', help='cpu, CUDA index, or intel:cpu for an OpenVINO runtime')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.runs < 1 or args.threads < 1 or any(size < 320 or size % 32 for size in args.imgsz):
        parser.error('Use positive runs/threads and image sizes divisible by 32, at least 320.')
    if any(not ((path.is_file() and path.suffix in ('.pt', '.onnx', '.engine')) or
                (path.is_dir() and (path.suffix == '.mlpackage' or path.name.endswith('_openvino_model')))) for path in args.weights):
        parser.error('Provide local PyTorch, ONNX, TensorRT, OpenVINO or CoreML artifacts.')
    if any(path.suffix != '.pt' for path in args.weights) and len(args.imgsz) != 1:
        parser.error('For static exports, pass one --imgsz matching the export input size.')
    with tempfile.TemporaryDirectory(prefix='vdm-weapon-benchmark-') as temporary:
        YOLO = configure_runtime(Path(temporary), args.threads)
        import cv2
        import numpy as np
        import torch
        image = args.image or Path(importlib.util.find_spec('ultralytics').origin).parent / 'assets' / 'bus.jpg'
        frame = cv2.imread(str(image))
        if frame is None:
            parser.error('Could not read image.')
        results = []
        for path in args.weights:
            model = YOLO(str(path.resolve()), task='detect')
            for size in args.imgsz:
                settings = dict(imgsz=size, device=args.device, rect=False, nms=False, conf=.25, max_det=30, verbose=False)
                for _ in range(3):
                    model.predict(frame, **settings)
                if list(model.predictor.imgsz) != [size, size]:
                    parser.error('Requested size differs from static export input; pass the export size.')
                cuda = model.predictor.device.type == 'cuda'
                if cuda:
                    torch.cuda.synchronize(model.predictor.device)
                timings = []
                for _ in range(args.runs):
                    started = time.perf_counter()
                    model.predict(frame, **settings)
                    if cuda:
                        torch.cuda.synchronize(model.predictor.device)
                    timings.append((time.perf_counter()-started)*1000)
                results.append(dict(weights=str(path), classes=len(model.names), input=[size, size],
                                    median_ms=round(float(np.median(timings)), 2), p95_ms=round(float(np.percentile(timings, 95)), 2)))
        report = dict(platform=platform.platform(), pytorch_cpu_threads=args.threads, device=args.device,
                      image=str(image), warmups=3, runs=args.runs, measurements=results,
                      note='Includes preprocessing, inference and postprocessing through the Ultralytics adapter. Exported runtimes use their own thread defaults. One-image latency only; not accuracy or full-pipeline camera capacity.')
        encoded = json.dumps(report, indent=2) + '\n'
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(encoded)
        print(encoded)


if __name__ == '__main__':
    main()
