"""Export a trained YOLO26 weapon candidate for a specific deployment runtime."""
import argparse
import importlib.metadata
import importlib.util
from pathlib import Path
import platform
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.train_weapons import artifact_digest, audit, configure_runtime, save_json

PROFILES = {
    'onnx-cpu': dict(format='onnx', quantize=32, simplify=False, opset=17),
    'openvino-fp32': dict(format='openvino', quantize=32),
    'openvino-int8': dict(format='openvino', quantize=8),
    'coreml-fp16': dict(format='coreml', quantize=16),
    'tensorrt-fp16': dict(format='engine', quantize=16, simplify=False, opset=17),
}
DEPENDENCIES = {
    'onnx-cpu': {'onnx': 'onnx>=1.17,<2', 'onnxruntime': 'onnxruntime>=1.20,<2'},
    'openvino-fp32': {'openvino': 'openvino>=2025.2,<2027'},
    'openvino-int8': {'openvino': 'openvino>=2025.2,<2027', 'nncf': 'nncf>=2.14,<4'},
    'coreml-fp16': {'coremltools': 'coremltools>=9,<10 (with numpy<=2.3.5)'},
    'tensorrt-fp16': {'tensorrt': 'TensorRT >=8.5 matching the target CUDA/JetPack installation', 'onnx': 'onnx>=1.17,<2'},
}


def check_target(target, device):
    if target == 'coreml-fp16' and platform.system() != 'Darwin':
        raise ValueError('Run CoreML conversion and validation on a Mac using the downloaded best.pt checkpoint.')
    if target == 'tensorrt-fp16':
        import torch
        if not device.isdigit() or not torch.cuda.is_available():
            raise ValueError('TensorRT requires the target NVIDIA CUDA device, e.g. --device 0. Build on that device.')
    elif device != 'cpu':
        raise ValueError('Use --device cpu for this conversion profile; the deployment device is selected at inference.')
    missing = [spec for module, spec in DEPENDENCIES[target].items() if importlib.util.find_spec(module) is None]
    if missing:
        raise ValueError('Install target dependencies first: ' + '; '.join(missing))
    if target == 'tensorrt-fp16':
        from packaging.version import Version
        import tensorrt
        if Version(tensorrt.__version__) < Version('8.5'):
            raise ValueError('TensorRT >=8.5 is required for the NMS-free head.')


def export_candidate(args):
    import yaml
    if not args.weights.is_file() or args.weights.suffix != '.pt':
        raise ValueError('Export needs an existing, trusted local best.pt checkpoint.')
    if args.output.exists():
        raise ValueError('Choose a new output directory; previous exports are preserved.')
    check_target(args.target, args.device)
    report = audit(args.data, args.imgsz, args.allow_upstream_annotations)
    if not report['valid']:
        raise ValueError('Dataset check failed: ' + '; '.join(report['errors'][:10]))
    args.output.mkdir(parents=True)
    YOLO = configure_runtime(args.output, args.threads)
    checkpoint = args.output / 'candidate.pt'
    shutil.copy2(args.weights, checkpoint)
    model = YOLO(str(checkpoint), task='detect')
    names = model.names
    names = [names[i] for i in range(len(names))] if isinstance(names, dict) else list(names)
    if names != report['names']:
        raise ValueError('Checkpoint classes/order differ from dataset. Train on this dataset before exporting.')
    if getattr(model.model.model[-1], 'one2one_cv2', None) is None:
        raise ValueError('These profiles require a YOLO26 checkpoint with its NMS-free head.')
    options = dict(PROFILES[args.target], imgsz=args.imgsz, batch=1, dynamic=False, nms=False, device=args.device)
    calibration = None
    if args.target == 'openvino-int8':
        # Both entries point at training images, even if an exporter falls back to val.
        config = dict(report['config'])
        config['val'] = config['train']
        config.pop('test', None)
        calibration = args.output / 'calibration-train-only.yaml'
        calibration.write_text(yaml.safe_dump(config, sort_keys=False))
        options.update(data=str(calibration), split='train', fraction=args.calibration_fraction)
    artifact = Path(model.export(**options)).resolve()
    if not artifact.exists():
        raise ValueError('Exporter did not produce an artifact.')
    versions = {}
    for package in ('ultralytics', 'torch', 'onnx', 'onnxruntime', 'openvino', 'nncf', 'coremltools', 'tensorrt'):
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            pass
    manifest = dict(candidate_only=True, target=args.target, artifact=artifact.relative_to(args.output).as_posix(),
        artifact_sha256=artifact_digest(artifact), checkpoint_sha256=artifact_digest(args.weights), names=names,
        dataset_sha256=report['dataset_sha256'], input_shape=[1, 3, args.imgsz, args.imgsz],
        nms_free=True, output='xyxy pixel coordinates in letterboxed input, score, class_id; undo letterboxing for display',
        calibration_split='train' if calibration else None, options=options, versions=versions,
        conversion_host=platform.platform(), validation_required=True,
        note='Measure accuracy and latency on target hardware. TensorRT engines depend on GPU and TensorRT/CUDA versions.')
    save_json(args.output / 'candidate.json', manifest)
    save_json(args.output / 'dataset-audit.json', report)
    print(f'Candidate export: {artifact}\nManifest: {args.output / "candidate.json"}')
    return artifact


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--weights', type=Path, required=True)
    parser.add_argument('--data', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--target', choices=PROFILES, default='onnx-cpu')
    parser.add_argument('--device', default='cpu')
    parser.add_argument('--imgsz', type=int, default=640)
    parser.add_argument('--threads', type=int, default=1)
    parser.add_argument('--calibration-fraction', type=float, default=1., help='Fraction of training images for INT8, (0,1]')
    parser.add_argument('--allow-upstream-annotations', action='store_true')
    args = parser.parse_args(argv)
    args.weights, args.data, args.output = (path.resolve() for path in (args.weights, args.data, args.output))
    if args.imgsz < 320 or args.imgsz % 32 or args.threads < 1 or not 0 < args.calibration_fraction <= 1:
        parser.error('Use imgsz >=320 divisible by 32, positive threads, and calibration fraction in (0,1].')
    try:
        export_candidate(args)
        return 0
    except (ValueError, OSError, ImportError) as exc:
        parser.exit(2, f'{exc}\n')


if __name__ == '__main__':
    sys.exit(main())
