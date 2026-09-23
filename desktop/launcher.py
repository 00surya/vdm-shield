"""Packaged loopback launcher; browser UI, per-user data, fixed cloud configuration."""
import json
import os
from pathlib import Path
import platform
import shutil
import sys
import webbrowser
import threading
import logging


def user_directory():
    if platform.system() == 'Darwin':
        return Path.home() / 'Library' / 'Application Support' / 'VDM'
    if platform.system() == 'Windows':
        return Path(os.environ['LOCALAPPDATA']) / 'VDM'
    raise RuntimeError('This desktop build supports macOS and Windows only.')


def prepare(root, destination):
    config = json.loads((root / 'desktop-config.json').read_text())
    from vmd.licensing import CloudTransport
    CloudTransport(config['cloud_url'], allow_local=config.get('local_demo') is True)
    os.environ['VDM_CLOUD_URL'] = config['cloud_url']
    os.environ['VDM_REQUIRE_LICENCE'] = '1'
    os.environ['VDM_CLOUD_ALLOW_LOCAL'] = '1' if config.get('local_demo') is True else '0'
    destination.mkdir(parents=True, exist_ok=True)
    models = destination / 'models'
    # Only curated shipped assets, never the developer's data or learned models.
    for source in (root / 'model-assets').iterdir():
        target = models / source.name
        if not target.exists():
            models.mkdir(exist_ok=True)
            temporary = models / (source.name + '.installing')
            if temporary.exists():
                shutil.rmtree(temporary) if temporary.is_dir() else temporary.unlink()
            if source.is_dir():
                shutil.copytree(source, temporary)
            else:
                shutil.copy2(source, temporary)
            temporary.rename(target)
    return destination / 'data', models


def self_test(root):
    """Exercise the frozen runtime with synthetic pixels in temporary local storage."""
    import tempfile
    with tempfile.TemporaryDirectory(prefix='vdm-bundle-test-') as temporary:
        data, models = prepare(root, Path(temporary))
        import cv2
        import numpy as np
        from vmd.vision import PoseModel, DepthModel
        from vmd.objects import ObjectModel
        from vmd.threats import ThreatModel
        frame = np.zeros((256, 256, 3), dtype=np.uint8)
        PoseModel(models / 'yolo11n-pose.pt', 'cpu').infer(frame)
        depth = DepthModel('cpu', models).infer(frame)
        assert depth.shape == frame.shape[:2] and np.isfinite(depth).all()
        ObjectModel(models).infer(frame)
        ThreatModel(models).infer(frame)
        from vmd.temporal import Encoder
        Encoder(models)
        from vmd.behavior import BehaviorHeuristic, EVENT_LABELS
        assert BehaviorHeuristic().confirmation.required_seconds >= 5
        assert EVENT_LABELS['fight'] == 'POSSIBLE FIGHT / REVIEW'
        import imageio_ffmpeg
        assert Path(imageio_ffmpeg.get_ffmpeg_exe()).is_file()
        from vmd.licensing import SecureStore
        SecureStore('https://test.invalid', temporary).backend()
        from vmd.app import create_app
        from unittest.mock import patch
        with patch('vmd.licensing.LicenceClient.start'):
            app = create_app(data, models)
        try:
            client = app.test_client()
            assert client.get('/').status_code == 200
            assert client.get('/activation').status_code == 200
            assert client.get('/static/main.js').status_code == 200
        finally:
            app.extensions['vdm_licence'].close()
            app.extensions['vmd_manager'].close()
    print('PASS: frozen model inference, five-second fight confirmation, encoder, ffmpeg path, native keyring import and UI routes. No camera, cloud or keychain roundtrip tested.')


class DesktopInstance:
    """OS-released lock protects one desktop process per per-user data directory."""
    def __init__(self, destination):
        self.destination = destination
        self.stream = None

    def acquire(self):
        self.destination.mkdir(parents=True, exist_ok=True)
        self.stream = (self.destination / 'desktop.lock').open('a+b')
        self.stream.seek(0)
        if not self.stream.read(1):
            self.stream.write(b'0')
            self.stream.flush()
        self.stream.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.stream.close()
            self.stream = None
            return False
        (self.destination / 'desktop-port.json').unlink(missing_ok=True)
        return True

    def publish_port(self, port):
        path = self.destination / 'desktop-port.json'
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps({'port': port}))
        temporary.replace(path)

    def existing_url(self):
        try:
            port = json.loads((self.destination / 'desktop-port.json').read_text())['port']
            if type(port) is int and 1 <= port <= 65535:
                return f'http://127.0.0.1:{port}/activation'
        except (OSError, ValueError, TypeError, KeyError):
            pass
        return None

    def close(self):
        if self.stream:
            try:
                (self.destination / 'desktop-port.json').unlink(missing_ok=True)
            finally:
                self.stream.close()
                self.stream = None


def create_desktop_server():
    from werkzeug.serving import make_server
    from flask import Flask
    # Port zero asks the OS for an available port atomically, without a probe race.
    return make_server('127.0.0.1', 0, Flask('startup'), threaded=True)


def window_test(report):
    import webview
    from flask import Flask
    app = Flask('window-test')
    app.add_url_rule('/', view_func=lambda: '<html><head><title>VDM</title></head><body><h1 id="ready">VDM desktop ready</h1></body></html>')
    server = create_desktop_server()
    server.app = app
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    window = webview.create_window('VDM · launch test', f'http://127.0.0.1:{server.server_port}', width=900, height=620)
    outcome = {'passed': False}
    def verify():
        try:
            if not window.events.loaded.wait(30):
                raise RuntimeError('Native window did not finish loading')
            outcome['passed'] = window.evaluate_js('document.getElementById("ready").textContent') == 'VDM desktop ready'
        except Exception as exc:
            outcome['error'] = str(exc)
        finally:
            report.write_text(json.dumps(outcome))
            window.destroy()
    try:
        webview.start(verify, private_mode=True)
    finally:
        server.shutdown()
        worker.join(timeout=10)
        server.server_close()
    if not outcome['passed']:
        raise RuntimeError('Native window test failed')


def main():
    from multiprocessing import freeze_support
    freeze_support()
    root = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))
    if '--window-test' in sys.argv:
        window_test(Path(sys.argv[sys.argv.index('--window-test') + 1]).resolve())
        return
    if '--self-test' in sys.argv:
        self_test(root)
        return
    destination = user_directory()
    destination.mkdir(parents=True, exist_ok=True)
    if getattr(sys, 'frozen', False):
        from logging.handlers import RotatingFileHandler
        handler = RotatingFileHandler(destination / 'desktop.log', maxBytes=1000000, backupCount=2)
        logging.basicConfig(handlers=[handler], level=logging.WARNING)
    instance = DesktopInstance(destination)
    if not instance.acquire():
        url = instance.existing_url()
        if url:
            print(f'VDM is already running at {url}. Opening the existing window.', flush=True)
            webbrowser.open(url)
        else:
            print('VDM is already starting. Wait for its browser window to open.', flush=True)
        return
    app = server = None
    try:
        server = create_desktop_server()
        data, models = prepare(root, destination)
        os.chdir(destination)
        from vmd.app import create_app
        app = create_app(data, models)
        server.app = app
        instance.publish_port(server.server_port)
        url = f'http://127.0.0.1:{server.server_port}/activation'
        if platform.system() == 'Darwin' and getattr(sys, 'frozen', False):
            import webview
            worker = threading.Thread(target=server.serve_forever, name='desktop-http', daemon=True)
            worker.start()
            try:
                webview.create_window('VDM', url, width=1280, height=850, min_size=(900, 620))
                webview.start(private_mode=True)
            finally:
                server.shutdown()
                worker.join(timeout=10)
        else:
            print(f'VDM is running at {url}. Keep this window open; close it to stop the application.', flush=True)
            webbrowser.open(url)
            server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if server:
            server.server_close()
        if app:
            app.extensions['vdm_licence'].close()
            app.extensions['vmd_manager'].close()
        instance.close()


if __name__ == '__main__':
    try:
        main()
    except Exception:
        logging.exception('VDM failed to start')
        if platform.system() == 'Darwin' and getattr(sys, 'frozen', False) and '--window-test' not in sys.argv and '--self-test' not in sys.argv:
            import subprocess
            subprocess.run(['osascript', '-e', 'display alert "VDM could not start" message "See ~/Library/Application Support/VDM/desktop.log for details."'], check=False)
        raise
