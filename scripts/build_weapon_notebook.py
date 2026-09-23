"""Build the self-contained Colab notebook and a small, uploadable training kit ZIP."""
import ast
import hashlib
import json
from pathlib import Path
import textwrap
import zipfile

ROOT = Path(__file__).resolve().parents[1]
FILES = [
    'scripts/prepare_weapon_data.py', 'scripts/train_weapons.py',
    'scripts/export_weapons.py', 'scripts/benchmark_weapons.py',
    'requirements-weapon-training.txt', 'docs/weapon-colab.md', 'docs/weapon-training.md',
]


def build():
    cells = []

    def cell(kind, source, **metadata):
        source = textwrap.dedent(source).strip() + '\n'
        if kind == 'code':
            ast.parse(source)
        item = dict(cell_type=kind, metadata=metadata, source=source.splitlines(keepends=True),
                    id=f'cell-{len(cells):02d}')
        if kind == 'code':
            item.update(execution_count=None, outputs=[])
        cells.append(item)

    cell('markdown', '''
        # Train a lightweight weapon detector — YOLO26n

        **Start here:** Runtime → Change runtime type → **T4 GPU**, then run cells in order.
        This notebook contains its own scripts. No dataset account, token or repository clone is needed.
        It downloads public data, prepares YOLO boxes, trains a candidate, evaluates it and exports ONNX.
        Google Drive can preserve checkpoints; the last cell downloads a results ZIP.

        **Coverage: gun, knife, grenade.** No generic bomb or explosion detector is trained here.
        The public labels are a starting point: a spot-check found missing boxes (one known bad image
        is excluded), and the dataset has **no weapon-free negative images**. Review/correct annotations
        and add realistic CCTV negatives before production training. 100 epochs does not guarantee accuracy.
        Camera/video groups are unknown; the starter test split is not proof of generalization to new cameras.

        Source: [fcakyon / ashish dataset](https://huggingface.co/datasets/fcakyon/gun-object-detection),
        declared CC BY 4.0. Attribution is retained. [Ultralytics licensing](https://www.ultralytics.com/license)
        also applies to the framework and model in a commercial product.
        No changes are made to the application's active detector.
    ''')
    cell('markdown', '## 1. Unpack the included scripts\nExpand the next cell to inspect the embedded, editable source files.')
    payload = {name: (ROOT / name).read_text() for name in FILES}
    cell('code', 'from pathlib import Path\nimport os, sys, subprocess, json, hashlib\n'
         'KIT = Path("/content/weapon-training-kit")\nKIT.mkdir(parents=True, exist_ok=True)\n'
         'EMBEDDED_FILES = ' + json.dumps(payload, indent=2) + '\n'
         'for name, source in EMBEDDED_FILES.items():\n'
         '    destination = KIT / name\n    destination.parent.mkdir(parents=True, exist_ok=True)\n'
         '    destination.write_text(source)\n'
         'os.chdir(KIT)\n'
         'if str(KIT) not in sys.path: sys.path.insert(0, str(KIT))\n'
         'def run(*args):\n    subprocess.run([sys.executable, *map(str, args)], check=True)\n'
         'print("Scripts written to", KIT)\n', cellView='form')
    cell('code', '''
        run('-m', 'pip', 'install', '-q', '-r', KIT / 'requirements-weapon-training.txt')
        import torch
        import ultralytics
        print('PyTorch:', torch.__version__, 'Ultralytics:', ultralytics.__version__)
        assert torch.cuda.is_available(), 'Select a GPU runtime before training (Runtime → Change runtime type).'
        print('GPU:', torch.cuda.get_device_name(0))
    ''')
    cell('markdown', '''
        ## 2. Choose training settings and persistent storage
        Start at 640 pixels and batch 8. Reduce batch to 4 or 2 if GPU memory runs out.
        Save to Drive to keep `best.pt` and `last.pt` if Colab disconnects. Without Drive, download
        the results before the runtime resets. Use a fresh run name for a new experiment.
    ''')
    cell('code', '''
        from datetime import datetime, timezone
        USE_GOOGLE_DRIVE = True  # @param {type:"boolean"}
        EPOCHS = 100  # @param {type:"integer"}
        IMAGE_SIZE = 640  # @param [320, 480, 640] {type:"raw"}
        BATCH_SIZE = 8  # @param {type:"integer"}
        if USE_GOOGLE_DRIVE:
            from google.colab import drive
            drive.mount('/content/drive')
            OUTPUT_ROOT = Path('/content/drive/MyDrive/vdm-weapon-training')
        else:
            OUTPUT_ROOT = KIT / 'runs'
        OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
        RUN_ID = 'weapons-' + datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
        TRAIN_OUT = OUTPUT_ROOT / RUN_ID
        DATA_ROOT = KIT / 'data/weapon_starter'
        DATA = DATA_ROOT / 'dataset.yaml'
        BASE_WEIGHTS = KIT / 'models/yolo26n.pt'
        BEST = TRAIN_OUT / 'fit/weights/best.pt'
        UPSTREAM = ['--allow-upstream-annotations']
        print('Run:', TRAIN_OUT)
    ''')
    cell('markdown', '''
        ## 3. Download and prepare data
        Downloads ~92 MB plus ~5.5 MB of official pretrained weights, with pinned checksums.
        COCO boxes become YOLO boxes; pistol/rifle merge as **gun**. Seed 42 produces
        **3,209 train / 551 validation / 905 test** images after excluding one known incomplete label.
        The original validation partition becomes test; validation is held out from original training.
        Exact duplicate pixels and filename families are checked, but unknown video/scene overlap can remain.
    ''')
    cell('code', '''
        if not DATA_ROOT.exists():
            run('scripts/prepare_weapon_data.py', '--cache', KIT / 'data/downloads',
                '--output', DATA_ROOT, '--weights', BASE_WEIGHTS)
        else:
            from scripts.prepare_weapon_data import download, WEIGHTS_URL, WEIGHTS_SHA
            download(WEIGHTS_URL, BASE_WEIGHTS, WEIGHTS_SHA, 6_000_000)
            print('Reusing existing prepared data; auditing it again below.')
        run('scripts/train_weapons.py', 'check', '--data', DATA, '--imgsz', IMAGE_SIZE, *UPSTREAM)
    ''')
    cell('code', '''
        from IPython.display import display, Image
        display(Image(filename=str(DATA_ROOT / 'preview.jpg')))
        print((DATA_ROOT / 'preparation.json').read_text())
    ''')
    cell('markdown', '''
        Inspect the boxes above. More missing/incorrect boxes may remain. To improve this dataset,
        edit the YOLO labels in an annotation tool and add reviewed CCTV negatives, keeping videos/sites
        together. The scripts retain `reviewed=upstream`; this experiment flag does not claim human review.
        Re-run the audit after edits. Do not tune thresholds against the reserved test set.

        ## 4. Train the candidate
        Fine-tunes COCO-pretrained YOLO26n with its NMS-free head. Training logs, metrics,
        class order, data fingerprint and best/last checkpoints are saved under the run directory.
        A one-epoch run is only a pipeline check, not a useful trained detector.
    ''')
    cell('code', '''
        run('scripts/train_weapons.py', 'train', '--data', DATA, *UPSTREAM,
            '--weights', BASE_WEIGHTS, '--output', TRAIN_OUT, '--device', '0',
            '--imgsz', IMAGE_SIZE, '--epochs', EPOCHS, '--batch', BATCH_SIZE, '--workers', '2')
        assert BEST.is_file(), 'Training did not produce best.pt; inspect the training log.'
        print('Candidate checkpoint:', BEST)
    ''')
    cell('markdown', '''
        **After a disconnected session:** re-create the scripts/environment and prepared data first.
        In a separate cell, use the saved Drive checkpoint with
        `YOLO('/content/drive/MyDrive/vdm-weapon-training/<run>/fit/weights/last.pt').train(resume=True, device=0)`.
        Import `YOLO` from `ultralytics` first. Resuming requires an unfinished checkpoint and the same
        dataset paths/settings. Set `TRAIN_OUT` and `BEST` back to that run before executing later cells.

        ## 5. Validate PyTorch and export portable FP32 ONNX
        Compare validation results before and after conversion. Select the input size and thresholds
        using validation; reducing resolution can hide distant knives. Conversion success alone is
        not a quality or speed result.
    ''')
    cell('code', '''
        PT_VAL = OUTPUT_ROOT / (RUN_ID + '-pt-val')
        run('scripts/train_weapons.py', 'evaluate', '--data', DATA, *UPSTREAM,
            '--weights', BEST, '--output', PT_VAL, '--split', 'val', '--device', '0', '--imgsz', IMAGE_SIZE)
        ONNX_OUT = OUTPUT_ROOT / (RUN_ID + '-onnx')
        run('scripts/export_weapons.py', '--data', DATA, *UPSTREAM, '--weights', BEST,
            '--target', 'onnx-cpu', '--output', ONNX_OUT, '--imgsz', IMAGE_SIZE)
        ONNX = ONNX_OUT / 'candidate.onnx'
        ONNX_VAL = OUTPUT_ROOT / (RUN_ID + '-onnx-val')
        run('scripts/train_weapons.py', 'evaluate', '--data', DATA, *UPSTREAM,
            '--weights', ONNX, '--output', ONNX_VAL, '--split', 'val', '--imgsz', IMAGE_SIZE)
        for report in (PT_VAL / 'metrics.json', ONNX_VAL / 'metrics.json'):
            print(report.name, report.parent.name, report.read_text())
    ''')
    cell('markdown', '''
        ## 6. Optional Intel OpenVINO INT8 conversion
        Off by default. Calibration uses **only training images**, never validation or test.
        Colab CPU timing is not customer-device timing. Quantization can hurt recall; compare the
        validation metrics and then measure the actual Intel hardware.
    ''')
    cell('code', '''
        EXPORT_OPENVINO_INT8 = False  # @param {type:"boolean"}
        if EXPORT_OPENVINO_INT8:
            run('-m', 'pip', 'install', '-q', 'openvino>=2025.2,<2027', 'nncf>=2.14,<4')
            OV_OUT = OUTPUT_ROOT / (RUN_ID + '-openvino-int8')
            run('scripts/export_weapons.py', '--data', DATA, *UPSTREAM, '--weights', BEST,
                '--target', 'openvino-int8', '--output', OV_OUT, '--imgsz', IMAGE_SIZE)
            manifest = json.loads((OV_OUT / 'candidate.json').read_text())
            run('scripts/train_weapons.py', 'evaluate', '--data', DATA, *UPSTREAM,
                '--weights', OV_OUT / manifest['artifact'], '--split', 'val', '--imgsz', IMAGE_SIZE,
                '--output', OUTPUT_ROOT / (RUN_ID + '-openvino-int8-val'))
    ''')
    cell('markdown', '''
        ## 7. Timing and final held-out evaluation
        Timing includes preprocessing/inference/postprocessing for one image through the Python adapter.
        It does not establish real camera throughput or false alarms. Exported runtimes use their own
        threading defaults. Re-run the benchmark on target hardware alongside the existing detector.
        Enable final test evaluation only after choosing settings on validation.
    ''')
    cell('code', '''
        sample = next((DATA_ROOT / 'images/val').glob('*.jpg'))
        run('scripts/benchmark_weapons.py', '--weights', BEST, ONNX, '--image', sample,
            '--imgsz', IMAGE_SIZE, '--runs', '30', '--output', TRAIN_OUT / 'cpu-latency.json')
        RUN_FINAL_TEST = False  # @param {type:"boolean"}
        if RUN_FINAL_TEST:
            for label, weights, device in [('pt', BEST, '0'), ('onnx', ONNX, 'cpu')]:
                run('scripts/train_weapons.py', 'evaluate', '--data', DATA, *UPSTREAM,
                    '--weights', weights, '--device', device, '--split', 'test', '--imgsz', IMAGE_SIZE,
                    '--output', OUTPUT_ROOT / (RUN_ID + '-' + label + '-test'))
    ''')
    cell('markdown', '''
        ## 8. Download the candidate, reports and conversion scripts
        The results ZIP includes weights, ONNX, metrics, provenance, attribution and scripts.
        It excludes dataset images. On another computer, re-download/prepare the data to evaluate
        or calibrate exports. Keep attribution with the dataset and derived training records.

        Additional device commands (run after unpacking the kit, with matching data paths):
        - **Apple:** install `coremltools>=9,<10` and `numpy<=2.3.5` in a Mac conversion environment;
          run `scripts/export_weapons.py --target coreml-fp16` with your `--weights`, `--data`,
          `--output`, `--imgsz` and `--allow-upstream-annotations` arguments.
        - **NVIDIA/Jetson:** install the TensorRT version matching the target CUDA/JetPack runtime;
          use `--target tensorrt-fp16 --device 0`. Build and benchmark on that target GPU.
          A Colab-generated TensorRT engine is not a universal deployment artifact.
        - **Intel FP32:** install OpenVINO and use `--target openvino-fp32`.

        See `docs/weapon-colab.md` in the ZIP for full commands and validation guidance.
        A production decision needs reviewed CCTV, per-class recall and false alarms per camera-hour.
    ''')
    cell('code', '''
        import zipfile
        result_zip = OUTPUT_ROOT / (RUN_ID + '-results.zip')
        with zipfile.ZipFile(result_zip, 'w', zipfile.ZIP_DEFLATED) as archive:
            for folder in sorted(OUTPUT_ROOT.glob(RUN_ID + '*')):
                if folder.is_dir():
                    for path in sorted(folder.rglob('*')):
                        if path.is_file() and '.runtime' not in path.parts:
                            archive.write(path, 'runs/' + path.relative_to(OUTPUT_ROOT).as_posix())
            for name in EMBEDDED_FILES:
                archive.write(KIT / name, name)
            for name in ('dataset.yaml', 'sources.csv', 'preparation.json', 'dataset-audit.json', 'preview.jpg'):
                archive.write(DATA_ROOT / name, 'dataset-records/' + name)
            for path in (DATA_ROOT / 'attribution').iterdir():
                archive.write(path, 'dataset-records/attribution/' + path.name)
        print('Results:', result_zip, 'MB:', round(result_zip.stat().st_size / 1e6, 1))
        from google.colab import files
        files.download(str(result_zip))
    ''')
    notebook = dict(cells=cells, metadata=dict(
        kernelspec=dict(display_name='Python 3', language='python', name='python3'),
        language_info=dict(name='python'), accelerator='GPU',
        colab=dict(name='weapon_training_colab.ipynb', provenance=[]),
        embedded_source_sha256={name: hashlib.sha256(source.encode()).hexdigest() for name, source in payload.items()}),
        nbformat=4, nbformat_minor=5)
    destination = ROOT / 'notebooks/weapon_training_colab.ipynb'
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(notebook, indent=2) + '\n')
    bundle = ROOT / 'artifacts/weapon-training-kit.zip'
    bundle.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(bundle, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name in [*FILES, 'scripts/build_weapon_notebook.py', 'notebooks/weapon_training_colab.ipynb']:
            archive.write(ROOT / name, name)
    print(destination)
    print(bundle)
    return destination, bundle


if __name__ == '__main__':
    build()
