"""Local development launcher with an explicit, browser-visible email preview."""
from pathlib import Path
import secrets
import argparse
from vdm_cloud.app import create_app
from vdm_cloud.models import Base

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--grant-demo', type=int, metavar='ORGANISATION_ID', help='Grant a local seven-day, one-device demo licence and exit')
    args = parser.parse_args()
    folder = Path('data/cloud-dev').resolve()
    folder.mkdir(parents=True, exist_ok=True)
    secret_file = folder / 'secret'
    if not secret_file.exists():
        secret_file.touch(mode=0o600)
        secret_file.write_text(secrets.token_urlsafe(48))
    app = create_app({
        'SECRET_KEY': secret_file.read_text().strip(),
        'DATABASE_URL': f'sqlite:///{folder / "accounts.sqlite"}',
        'SESSION_COOKIE_SECURE': False,
        'PUBLIC_URL': 'http://127.0.0.1:8766',
        'DEVELOPMENT_EMAIL_PREVIEW': True,
        'VDM_RELEASE_DIR': str(Path('output/releases-local').resolve()),
        'ALLOW_DEVELOPMENT_DOWNLOADS': True,
        'MAIL_DELIVERY': lambda email, link: print(f'\nDEVELOPMENT ONLY — account link for {email}:\n{link}\n'),
    })
    Base.metadata.create_all(app.extensions['cloud_engine'])
    if args.grant_demo is not None:
        with app.app_context():
            app.cli.main(args=['grant-licence', '--organisation-id', str(args.grant_demo), '--days', '7'], standalone_mode=False)
    else:
        app.run(host='127.0.0.1', port=8766, debug=False)
