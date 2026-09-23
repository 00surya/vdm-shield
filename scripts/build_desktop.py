"""Build a native, unsigned test bundle. Run separately on macOS and Windows."""
import argparse
import os
import importlib.util
from importlib.metadata import version
import json
import platform
import plistlib
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ['zipdepth-91f3fd2.onnx', 'zipdepth-91f3fd2.json', 'ZipDepth-LICENSE.txt', 'yolo11n-pose.pt',
          'yolo26s.pt', 'threat-yolov8n.pt', 'resnet18-f37072fd.pth']


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--cloud-url', required=True)
    parser.add_argument('--local-demo', action='store_true')
    parser.add_argument('--check', action='store_true', help='Validate inputs without building')
    parser.add_argument('--output-dir', type=Path, default=ROOT / 'output' / 'desktop')
    parser.add_argument('--version', default='0.2.0')
    args = parser.parse_args()
    sys.path.insert(0, str(ROOT))
    from vmd.licensing import CloudTransport
    CloudTransport(args.cloud_url, args.local_demo)
    if platform.system() not in {'Darwin', 'Windows'}:
        raise SystemExit('Build on macOS or Windows, matching the target architecture.')
    missing = [name for name in ['PyInstaller', 'flask', 'cv2', 'torch', 'torchvision', 'ultralytics', 'onnxruntime', 'keyring', 'imageio_ffmpeg'] if importlib.util.find_spec(name) is None]
    if platform.system() == 'Darwin':
        missing += [name for name in ['webview', 'Cocoa', 'WebKit', 'Quartz'] if importlib.util.find_spec(name) is None]
    missing += [str(ROOT / 'models' / name) for name in ASSETS if not (ROOT / 'models' / name).exists()]
    if missing:
        raise SystemExit('Missing build requirements: ' + ', '.join(missing))
    try:
        from packaging.version import Version
        for package in ['numpy', 'torch', 'torchvision', 'opencv-python', 'vmd-shield']:
            Version(version(package))
    except Exception as exc:
        raise SystemExit(f'Build environment metadata is invalid ({package}). Create a clean Python 3.11 virtual environment and install requirements-desktop-build.txt. Details: {exc}')
    if args.check:
        print('Build inputs present. This does not validate inference or signing.')
        return
    staging = ROOT / 'tmp' / 'desktop-build-groups'
    staging.mkdir(parents=True, exist_ok=True)
    config = staging / 'desktop-config.json'
    config.write_text(json.dumps({'cloud_url': args.cloud_url, 'local_demo': args.local_demo}))
    target = platform.system().lower() + '-' + platform.machine().lower()
    command = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--onedir', '--name', 'VDM',
               '--distpath', str(args.output_dir.resolve() / target), '--workpath', str(staging / 'work'),
               '--specpath', str(staging), '--paths', str(ROOT), '--copy-metadata', 'vmd-shield',
               '--add-data', f'{config}:.', '--collect-data', 'vmd']
    if platform.system() == 'Darwin':
        command += ['--windowed', '--osx-bundle-identifier', 'com.vdm.desktop', '--icon', str(ROOT / 'desktop' / 'VDM.icns'), '--collect-all', 'webview', '--hidden-import', 'webview.platforms.cocoa']
    for package in ['ultralytics', 'onnxruntime', 'imageio_ffmpeg', 'keyring', 'torchvision']:
        command += ['--collect-all', package]
    command += ['--hidden-import', 'lap']
    for name in ASSETS:
        source = ROOT / 'models' / name
        destination = 'model-assets/' + name if source.is_dir() else 'model-assets'
        command += ['--add-data', f'{source}:{destination}']
    command.append(str(ROOT / 'desktop' / 'launcher.py'))
    build_env = dict(os.environ)
    build_env['PYINSTALLER_CONFIG_DIR'] = str(staging / 'cache')
    build_env['MPLCONFIGDIR'] = str(staging / 'matplotlib')
    build_env['YOLO_CONFIG_DIR'] = str(staging / 'ultralytics')
    build_env['YOLO_OFFLINE'] = 'true'
    build_env['YOLO_AUTOINSTALL'] = 'false'
    subprocess.run(command, cwd=ROOT, env=build_env, check=True)
    if platform.system() == 'Darwin':
        bundle = args.output_dir.resolve() / target / 'VDM.app'
        info_path = bundle / 'Contents' / 'Info.plist'
        info = plistlib.loads(info_path.read_bytes())
        info.update(NSCameraUsageDescription='VDM uses the camera you select for local video analysis.',
                    NSHighResolutionCapable=True, CFBundleShortVersionString=args.version,
                    CFBundleVersion=args.version)
        info_path.write_bytes(plistlib.dumps(info))
        # package_macos.py removes Finder metadata and seals a clean temporary copy.
    print('Native development bundle created. Test native inference and secure storage before publishing.')


if __name__ == '__main__':
    main()
