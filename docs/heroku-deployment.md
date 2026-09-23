# Heroku cloud deployment

## Live deployment

Deployed and verified on 24 September 2026:

- Website: https://vdm-shield-c497900ed597.herokuapp.com
- Heroku dashboard: https://dashboard.heroku.com/apps/vdm-shield
- App: `vdm-shield`, EU region, Heroku-24, Python 3.13.
- Release: `v7`, GitHub commit `1bef399`, successful database initialization.
- Web: one always-on Basic dyno ($7/month).
- Database: `postgresql-clear-16129`, Essential-0 PostgreSQL ($5/month).
- Approved recurring base cost: approximately US$12/month, excluding taxes and
  email service costs. No email add-on has been provisioned.

Live checks passed for the product, login and signup pages, `/health`, all checked
animation assets and fonts, secure session cookies, CSRF rejection, and a
PostgreSQL-backed invalid-login rejection. No accounts were created by these checks.

Email is not configured yet. Registration, verification resend and password-reset
requests now show their unavailability before collecting details or creating new
records. Existing verified accounts can still sign in. The forms become available
once the required mail settings are present; real delivery still needs validation.
See [email setup](cloud-email-setup.md). Local accounts were not migrated. Desktop
installers have not been published to this deployment.

The Heroku app serves the public product website and the account, group, licence
and administrative-log workspace. Video capture, inference and local learning
remain on the device. The original cloud-only deployment contains `vdm_cloud`,
its static assets and templates, the stdlib-only shared validator `vmd/metadata.py`
with its package initializer, and the Python/Heroku launch files.

## GitHub deployments

Repository: [00surya/vdm-shield](https://github.com/00surya/vdm-shield), public,
with `main` as its default branch. All 43 cloud checks passed on GitHub's clean
Python 3.13 runner. Release `v7` was built from that exact public GitHub commit
using Heroku's Build API; its release succeeded, the web dyno is up, and the
health, product, login and signup pages returned HTTP 200.

**Automatic deployments are not enabled yet.** Heroku's account-level GitHub
authorization is still pending. The verified Build API deployment is a manual
release, not a webhook connection; a future push to `main` will run tests but
will not yet deploy the website.

The full project can deploy from its repository root: `requirements.txt` selects
the cloud dependencies and `Procfile` starts only the cloud service. `.slugignore`
removes documentation, tests, training tools and device UI assets from the slug.
The source repository also includes device Python modules, but Heroku does not
install the vision extras, download model weights or start camera workers.

The `Cloud checks` GitHub Actions workflow runs the account, group, licensing and
numeric-metadata tests on Python 3.13. It uses read-only repository access and does
not require production credentials.

To enable native GitHub deployment, connect the repository under the app's
**Deploy → GitHub** tab. Select `main`, enable **Wait for CI to pass before deploy**,
then enable automatic deployments. Deploy the branch once to verify the initial
connection. This uses the existing app and database; Heroku CI, review apps and a
second staging app are not needed. Keep SMTP and database credentials in Heroku,
not GitHub source or workflow files.

Account authorization and a verified push-triggered release are required before
treating automatic deployment as configured. After connecting, use GitHub as the
deployment source; its commits are not synchronized into the separate Heroku Git
repository.

## Prepare the deployment

```sh
.venv/bin/python scripts/prepare_heroku.py --output tmp/heroku-deploy
```

This uses an explicit file allowlist and creates a SHA-256 manifest. It excludes
local accounts, credentials, footage, datasets, model weights, installers and
training reports. The target must be new or empty. Use this isolated directory as
the deployment Git root for a manual cloud-only deployment; do not push a parent
Desktop repository. It is not needed for the GitHub workflow above.

Python 3.13 is selected through `.python-version`. The `release` process runs
`init-db` before the new web process starts. It creates missing tables and does
not perform destructive resets or migrate changed columns. Future schema changes
need explicit migrations.

## Required service configuration

- An authenticated Heroku account and the target app name.
- A persistent PostgreSQL database attached as `DATABASE_URL`.
- A new random `VDM_CLOUD_SECRET` of at least 32 characters, kept in Heroku config
  vars and stable across releases.
- `VDM_PUBLIC_URL`: the exact public HTTPS origin from the Heroku app details.
- `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `MAIL_FROM`: an existing
  verified sender for signup, invitations and password recovery. Set secrets in
  Heroku config vars, not in source code or chat. See `deploy/heroku.env.example`.

Do not enable the localhost email-preview flow in production. Without real email
delivery the landing page can load, but new users cannot complete registration.

Use an always-on web dyno for an operational licensing service: the desktop
checks short-lived permissions against this server. Confirm the selected dyno and
database plans in the account before provisioning resources.

## Deploy and verify

Create a dedicated Git repository in the generated deployment directory, commit
only its files, add the chosen app's Heroku Git remote, then push its main branch.
The web process uses Gunicorn and Heroku's assigned `PORT`. Keep real credentials
out of Git URLs and command-line arguments.

After the release completes, verify `/health`, `/`, `/product`, `/login`, `/signup`
and the product CSS/JavaScript, then perform signup and email verification with a
test account that you control. `/health` is process liveness, not a full database
or email check. Verify a database-backed flow separately.

Existing local accounts are not automatically uploaded to the new database.
Desktop clients must use the deployed origin through `VDM_CLOUD_URL`; installer
builds configured for localhost need a new release configuration.

The existing installer catalogue serves local files for development/staging.
Production installers need durable object storage and a published release
catalogue; they are not included in the web deployment bundle and should not be
stored on Heroku's temporary disk.

References:
- https://devcenter.heroku.com/articles/getting-started-with-python
- https://devcenter.heroku.com/articles/python-support
- https://devcenter.heroku.com/articles/release-phase
