#!/usr/bin/env python3
"""Start the local VMD Shield Flask server from the project root."""

import importlib.util
import argparse
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="Run the local VMD Shield server")
    parser.add_argument("--port", type=int, default=8765, help="loopback port (default: 8765)")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    if not (3, 10) <= sys.version_info[:2] < (3, 14):
        raise SystemExit("VMD Shield needs Python 3.10–3.13. See README.md for setup.")

    missing = [name for name in ("flask", "numpy", "cv2") if importlib.util.find_spec(name) is None]
    if missing:
        raise SystemExit(
            "Missing dependencies: " + ", ".join(missing) +
            "\nInstall the project first: python -m pip install -e '.[vision]'"
        )

    from vmd.app import create_app

    app = create_app(data_dir=Path(__file__).parent / "data", model_dir=Path(__file__).parent / "models")
    print(f"VMD Shield is running at http://127.0.0.1:{args.port}", flush=True)
    app.run(host="127.0.0.1", port=args.port, threaded=True, use_reloader=False)


if __name__ == "__main__":
    main()
