"""Install the pinned, checksum-verified community YOLOv8n threat weights."""
import hashlib
from pathlib import Path
from urllib.request import urlretrieve
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from vmd.threats import WEIGHTS, SHA256, MODEL_URL


def main():
    root = Path(__file__).resolve().parents[1] / "models"
    root.mkdir(exist_ok=True)
    path = root / WEIGHTS
    if path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == SHA256:
        print(f"Verified: {path}")
        return
    temporary = path.with_suffix(".download")
    urlretrieve(MODEL_URL, temporary)
    if hashlib.sha256(temporary.read_bytes()).hexdigest() != SHA256:
        temporary.unlink(missing_ok=True)
        raise RuntimeError("Downloaded threat weights failed checksum verification")
    temporary.replace(path)
    print(f"Installed: {path}")


if __name__ == "__main__":
    main()
