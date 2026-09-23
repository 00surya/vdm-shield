#!/usr/bin/env python3
"""Run VMD with a local Chromium kiosk on the attached display."""
import argparse
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from urllib.error import URLError
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parent


def find_browser(explicit=None):
    if explicit:
        found = shutil.which(explicit)
        if found:
            return found
        raise SystemExit("Browser executable not found: " + explicit)
    for name in ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable"):
        found = shutil.which(name)
        if found:
            return found
    mac_chrome = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    if mac_chrome.is_file():
        return str(mac_chrome)
    raise SystemExit("Install Chromium or Chrome on the device, or pass --browser /path/to/browser.")


def wait_for_server(server, url, timeout=60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if server.poll() is not None:
            raise RuntimeError("VMD server could not start. Check the terminal output.")
        try:
            with urlopen(url, timeout=1) as response:
                if response.status == 200:
                    return
        except (URLError, TimeoutError, OSError):
            pass
        time.sleep(.2)
    raise RuntimeError("VMD server did not become ready within 60 seconds.")


def stop(process):
    if process is not None and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--browser", help="Chromium or Chrome executable")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    browser = find_browser(args.browser)
    # Refuse an occupied port so the kiosk cannot attach to an unrelated service.
    import socket
    with socket.socket() as probe:
        try:
            probe.bind(("127.0.0.1", args.port))
        except OSError:
            raise SystemExit("Port is already in use. Stop the other server or choose --port.")
    server = display = None
    try:
        server = subprocess.Popen([sys.executable, str(ROOT / "run.py"), "--port", str(args.port)], cwd=ROOT)
        wait_for_server(server, f"http://127.0.0.1:{args.port}/")
        with tempfile.TemporaryDirectory(prefix="vmd-display-") as profile:
            display = subprocess.Popen([
                browser, "--kiosk", "--no-first-run", "--no-default-browser-check",
                f"--user-data-dir={profile}", f"http://127.0.0.1:{args.port}/#capacity",
            ])
            try:
                while display.poll() is None:
                    if server.poll() is not None:
                        raise RuntimeError("VMD server stopped.")
                    time.sleep(.5)
            finally:
                stop(display)
    except KeyboardInterrupt:
        pass
    except RuntimeError as error:
        raise SystemExit(str(error))
    finally:
        stop(server)


if __name__ == "__main__":
    main()
