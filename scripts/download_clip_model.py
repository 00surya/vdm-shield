"""Download the official ImageNet ResNet18 encoder with checksum verification."""
import hashlib
import sys
from pathlib import Path
from urllib.request import urlretrieve
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from vmd.temporal import ENCODER_FILE, ENCODER_HASH


def main():
    path = Path(__file__).resolve().parents[1] / 'models' / ENCODER_FILE
    path.parent.mkdir(exist_ok=True)
    if path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == ENCODER_HASH:
        print(f'Verified: {path}'); return
    temporary = path.with_suffix('.download')
    try:
        urlretrieve('https://download.pytorch.org/models/resnet18-f37072fd.pth', temporary)
        if hashlib.sha256(temporary.read_bytes()).hexdigest() != ENCODER_HASH:
            raise RuntimeError('ResNet18 checksum verification failed')
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    print(f'Installed: {path}')


if __name__ == '__main__':
    main()
