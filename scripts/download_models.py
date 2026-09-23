"""Download pose, depth, and object-threat assets into the project model directory."""
import argparse
import hashlib
import json
from pathlib import Path
from urllib.request import urlretrieve

destination = Path(__file__).resolve().parents[1] / "models"
destination.mkdir(exist_ok=True)


def download(url, target):
    if target.exists():
        print(f"Already present: {target}", flush=True)
        return
    temporary = target.with_suffix(".download")
    print(f"Downloading {target.name}", flush=True)
    urlretrieve(url, temporary)
    temporary.replace(target)
    print(f"Saved {target}", flush=True)


argparse.ArgumentParser(description=__doc__).parse_args()
download('https://github.com/ultralytics/assets/releases/download/v8.3.0/yolo11n-pose.pt', destination/'yolo11n-pose.pt')
from download_depth_model import install as install_depth
install_depth(destination)
from download_threat_model import main as download_threats
download_threats()
from download_object_model import main as download_objects
download_objects()
from download_clip_model import main as download_clip
download_clip()
manifest = {}
for weight in destination.glob('*.pt'):
    with weight.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest() if hasattr(hashlib, 'file_digest') else hashlib.sha256(stream.read()).hexdigest()
    manifest[weight.name] = {'bytes': weight.stat().st_size, 'sha256': digest}
(destination/'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
