"""Fail-closed download catalogue. Only explicitly published, hashed files are served."""
import hashlib
import json
from pathlib import Path
from flask import abort, render_template, send_file
from sqlalchemy.orm import Session


def register_downloads(app, engine, current_user):
    def catalogue():
        configured = app.config.get('VDM_RELEASE_DIR')
        if not configured:
            return None, []
        root = Path(configured).resolve()
        try:
            records = json.loads((root / 'releases.json').read_text())
            if not isinstance(records, list):
                raise ValueError()
            valid = []
            for row in records:
                allowed = {'published', 'development'} if app.config.get('ALLOW_DEVELOPMENT_DOWNLOADS') else {'published'}
                if row.get('platform') not in {'macos-arm64', 'macos-x86_64', 'windows-amd64'} or row.get('status') not in allowed:
                    continue
                name = row['filename']
                path = (root / name).resolve()
                if Path(name).name != name or path.parent != root or not path.is_file():
                    continue
                if row.get('size') != path.stat().st_size:
                    continue
                if not isinstance(row.get('sha256'), str) or len(row['sha256']) != 64:
                    continue
                valid.append(row)
            return root, valid
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            return root, []

    def require_member(db):
        user = current_user(db)
        if not user:
            abort(403)

    @app.get('/downloads')
    def downloads():
        with Session(engine) as db:
            require_member(db)
        _, releases = catalogue()
        return render_template('downloads.html', releases=releases)

    @app.get('/downloads/<platform>')
    def download_release(platform):
        with Session(engine) as db:
            require_member(db)
        root, releases = catalogue()
        release = next((row for row in releases if row['platform'] == platform), None)
        if release is None:
            abort(404)
        path = root / release['filename']
        # Verify before returning a trusted artifact; the release directory is service-owned.
        with path.open('rb') as stream:
            hasher = hashlib.sha256()
            for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                hasher.update(chunk)
            actual = hasher.hexdigest()
        if actual != release['sha256']:
            abort(503, 'Download temporarily unavailable: integrity verification failed.')
        return send_file(path, as_attachment=True, download_name=release['filename'])
