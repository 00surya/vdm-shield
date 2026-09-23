"""Install checksum-verified official YOLO26s scene object weights."""
import hashlib
import sys
from pathlib import Path
from urllib.request import urlretrieve

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from vmd.objects import WEIGHTS, MODEL_URL, SHA256


def main():
    path = Path(__file__).resolve().parents[1] / 'models' / WEIGHTS
    path.parent.mkdir(exist_ok=True)
    if path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == SHA256:
        print(f'Verified: {path}')
        return
    temporary = path.with_suffix('.download')
    try:
        urlretrieve(MODEL_URL, temporary)
        if hashlib.sha256(temporary.read_bytes()).hexdigest() != SHA256:
            raise RuntimeError('General object weights failed checksum verification')
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    print(f'Installed: {path}')


if __name__ == '__main__':
    main()
