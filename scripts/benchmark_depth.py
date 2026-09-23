"""Measure depth latency on identical images; this does not measure accuracy."""
import argparse
import importlib.util
import json
import platform
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cv2
import numpy as np
from vmd.depth import DepthModel

# Optional historical baseline, kept out of the production runtime.
class MidasReference:
    def __init__(self, device, model_dir):
        import torch
        self.torch = torch
        self.device = device
        # Local repository + local weights only: network setup is a separate explicit command.
        root = Path(model_dir)
        repo = root / "MiDaS"
        weight = root / "midas_v21_small_256.pt"
        if not repo.is_dir() or not weight.is_file():
            raise RuntimeError("Depth assets missing. The optional baseline needs the previous local MiDaS assets.")
        transforms = torch.hub.load(str(repo), "transforms", source="local")
        # Upstream Small fetches its architecture via torch.hub even with pretrained=False.
        # Redirect only that factory to a pinned local repository and load full weights below.
        import midas.blocks as blocks
        efficientnet = root / "efficientnet"
        if not efficientnet.is_dir():
            raise RuntimeError("Local EfficientNet sources missing. Run download_models.py.")
        original = blocks._make_pretrained_efficientnet_lite3
        def local_backbone(use_pretrained, exportable=False):
            net = torch.hub.load(str(efficientnet), "tf_efficientnet_lite3", source="local", pretrained=False, exportable=exportable)
            return blocks._make_efficientnet_backbone(net)
        blocks._make_pretrained_efficientnet_lite3 = local_backbone
        try:
            self.model = torch.hub.load(str(repo), "MiDaS_small", source="local", pretrained=False)
        finally:
            blocks._make_pretrained_efficientnet_lite3 = original
        self.model.load_state_dict(torch.load(weight, map_location="cpu", weights_only=True))
        self.model.to(device).eval()
        self.transform = transforms.small_transform

    def infer(self, frame):
        torch = self.torch
        with torch.inference_mode():
            batch = self.transform(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)).to(self.device)
            prediction = self.model(batch)
            output = torch.nn.functional.interpolate(prediction.unsqueeze(1), size=frame.shape[:2], mode="bicubic", align_corners=False).squeeze().cpu().numpy()
        low, high = np.percentile(output, (2, 98))
        return np.clip((output - low) / max(high-low, 1e-6), 0, 1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--compare-midas', action='store_true', help='Requires the previous MiDaS assets and timm==0.6.13')
    parser.add_argument('--runs', type=int, default=10)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.runs < 1:
        parser.error('--runs must be positive')
    cv2.setNumThreads(1)
    if args.compare_midas:
        import torch
        torch.set_num_threads(1)
    sample = Path(importlib.util.find_spec('ultralytics').origin).parent / 'assets/bus.jpg'
    frame = cv2.imread(str(sample))
    measurements = []
    models = [('MiDaS Small', MidasReference)] if args.compare_midas else []
    models.append(('ZipDepth ONNX', DepthModel))
    for name, factory in models:
        started = time.perf_counter()
        model = factory('cpu', Path(__file__).resolve().parents[1] / 'models')
        load_ms = (time.perf_counter()-started)*1000
        for width, height in [(384, 512), (960, 540)]:
            image = cv2.resize(frame, (width, height))
            for _ in range(2):
                model.infer(image)
            durations = []
            for _ in range(args.runs):
                started = time.perf_counter()
                depth = model.infer(image)
                durations.append((time.perf_counter()-started)*1000)
                assert depth.shape == image.shape[:2] and np.isfinite(depth).all()
            measurements.append(dict(model=name, frame=[width, height], runs=args.runs,
                                     load_ms=round(load_ms, 2), median_ms=round(statistics.median(durations), 2)))
    report = dict(platform=platform.platform(), cpu_threads=1, warmups=2,
                  sample='Ultralytics bundled bus.jpg (resized)', measurements=measurements)
    encoded = json.dumps(report, indent=2) + '\n'
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded)
    print(encoded)


if __name__ == '__main__':
    main()
