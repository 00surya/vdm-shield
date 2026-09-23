"""Stage a manually verified archive and its download catalogue; no network upload."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--artifact', type=Path, required=True)
    parser.add_argument('--platform', choices=['macos-arm64', 'macos-x86_64', 'windows-amd64'], required=True)
    parser.add_argument('--version', required=True)
    parser.add_argument('--release-dir', type=Path, required=True)
    parser.add_argument('--verified', action='store_true', help='Confirm native QA and signing checks have passed')
    parser.add_argument('--development', action='store_true', help='Stage a local preview; never mark it as a public release')
    args = parser.parse_args()
    if not args.verified and not args.development:
        parser.error('Verify the native release before publishing; see docs/desktop-distribution.md.')
    if not args.artifact.is_file() or args.artifact.suffix.lower() not in {'.zip', '.dmg', '.exe', '.msi'}:
        parser.error('Provide a completed ZIP, DMG, EXE or MSI artifact.')
    args.release_dir.mkdir(parents=True, exist_ok=True)
    destination = args.release_dir / args.artifact.name
    if destination.exists():
        parser.error('Use a unique artifact filename; published artifacts cannot be overwritten.')
    shutil.copy2(args.artifact, destination)
    digest = hashlib.sha256()
    with destination.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    manifest = args.release_dir / 'releases.json'
    rows = json.loads(manifest.read_text()) if manifest.exists() else []
    rows = [row for row in rows if row['platform'] != args.platform]
    rows.append(dict(platform=args.platform, version=args.version, filename=destination.name,
                     sha256=digest.hexdigest(), size=destination.stat().st_size,
                     status='development' if args.development else 'published'))
    temporary = manifest.with_suffix('.tmp')
    temporary.write_text(json.dumps(rows, indent=2))
    temporary.replace(manifest)
    print('Release staged locally. Configure VDM_RELEASE_DIR to serve it; no cloud upload was performed.')


if __name__ == '__main__':
    main()
