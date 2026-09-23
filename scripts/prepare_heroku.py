"""Prepare an isolated cloud-only deployment directory without copying local data.

Usage: python scripts/prepare_heroku.py --output tmp/heroku-deploy
The destination must be new or empty; no existing deployment is overwritten.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil


def prepare(root: Path, destination: Path):
    root = root.resolve()
    destination = destination.resolve()
    if destination.exists() and any(destination.iterdir()):
        raise ValueError("Choose a new or empty deployment directory.")
    allowed_root_files = ("Procfile", ".python-version", ".slugignore", ".gitignore", "requirements.txt", "requirements-cloud.txt")
    files = [root / name for name in allowed_root_files]
    # The cloud uses this stdlib-only shared numeric snapshot validator.
    files += [root / "vmd/__init__.py", root / "vmd/metadata.py"]
    # Static assets and templates are required; only these extensions are copied.
    allowed_suffixes = {".py", ".html", ".css", ".js", ".svg", ".ttf", ".txt"}
    files += [p for p in sorted((root / "vdm_cloud").rglob("*"))
              if p.is_file() and p.suffix in allowed_suffixes and "__pycache__" not in p.parts]
    if any(p.is_symlink() or not p.resolve().is_relative_to(root) for p in files):
        raise ValueError("Deployment sources must be regular project files, not external symlinks.")
    if any(not p.is_file() for p in files):
        raise ValueError("A required deployment file is missing.")
    destination.mkdir(parents=True, exist_ok=True)
    records = []
    for source in files:
        relative = source.relative_to(root)
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        records.append({"path": relative.as_posix(), "size": target.stat().st_size,
                        "sha256": hashlib.sha256(target.read_bytes()).hexdigest()})
    manifest = {"purpose": "VDM cloud-only Heroku deployment", "files": records,
                "total_bytes": sum(r["size"] for r in records)}
    (destination / "deployment-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("tmp/heroku-deploy"))
    args = parser.parse_args()
    manifest = prepare(Path(__file__).resolve().parents[1], args.output)
    print(json.dumps({"directory": str(args.output.resolve()), "files": len(manifest["files"]),
                      "bytes": manifest["total_bytes"]}, indent=2))
