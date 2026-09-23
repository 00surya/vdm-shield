# Organization website and group desktop access

The website and native Mac app implement this workflow:

1. An owner signs up and verifies their email. A configurable 14-day trial grants
   two installations, with up to four cameras per installation.
2. The administrator creates groups such as **Block B** and **North gate**.
3. Operators receive email invitations and choose their own passwords. Existing
   members can be added to more groups.
4. Members download the app and generate a one-time code for an accessible group.
5. They install VDM, enter the code, and connect cameras in that group's local workspace.

Device names are optional labels. Leave the field blank to use a group-based name
such as **Block B · Device 3**, or enter a custom name of up to 100 characters.
Name errors stay beside the field and preserve the selected group without
reserving a device slot.

## Permissions

| Capability | Administrator | Operator |
| --- | --- | --- |
| Create groups / change limits / invite people | Organization-wide | No |
| Download the desktop app | Yes | Yes |
| Generate activation codes | Organization groups | Assigned, enabled groups |
| View devices and numeric health | Organization-wide | Assigned groups |
| Revoke devices | Any organization device | Own enrolled devices |
| View administrative activity | Yes | No |
| Increase the purchased plan | Service-owner provisioning | No |

Multiple members can use a group. Each member receives a separate enrollment code;
website administrator powers come from the account role. One account currently
belongs to one organization, with any number of group memberships. Additional
administrator roles and cross-organization accounts are outside this version.

Group limits are caps within a shared organization device allowance. A group's
camera allowance cannot exceed the plan or the app's four-source cap. Codes reserve
a device slot for one hour, are displayed once and are consumed once. A code does
not create a website session or promote an operator.

Removing membership revokes that member's pending and active devices for removed
groups. Suspending a member revokes their sessions and all their enrollments. Other
members keep their devices. Restoring access permits new enrollments, not revival
of revoked credentials. Disabling a group denies activation, renewal, lease checks
and metadata ingestion; enabling it permits unrevoked devices to renew. Cached
permission lasts at most five minutes.

There is no paid checkout. Service owners use `grant-licence` to provision plans;
tenant administrators cannot change those limits or expiry. Set `VDM_TRIAL_DAYS=0`
to disable new trials. Reusing email-verification links never extends a trial.
Trials are not billing or legal organization-ownership verification.

## Run locally

```sh
.venv/bin/python -m pip install -r requirements-cloud.txt
.venv/bin/python run_cloud.py
```

Open **http://127.0.0.1:8766**. This launcher binds to localhost and keeps its SQLite
database in `data/cloud-dev`. Create your organization, select **Continue
verification** on the next page, choose your sign-in password, then sign in. No real
email is sent in this local preview. If you signed up before this step was added,
sign in with the original password to reach verification; you do not need another
account.

The launcher also prints verification, invitation and password-reset links. With
output redirected, these are in the corresponding log, currently
`tmp/cloud-preview.log`. Invitation and password-reset previews still use those
printed links. Local links do not verify a real mailbox.

`DEVELOPMENT_EMAIL_PREVIEW` is enabled only by `run_cloud.py`. It requires a
localhost public URL and development mail handler, rejects non-local requests,
and reveals a verification link only after the password for that pending account
has been checked. Production leaves this option off, sends mail through SMTP and
provides a rate-limited **Resend verification email** action. Do not expose the
development launcher publicly.

Downloads can serve the Apple Silicon preview staged in `output/releases-local`.
It is labeled **Development preview** and requires this website running on the same
Mac for activation and renewal. Production configuration hides development artifacts.

Open the DMG, drag VDM to Applications, launch it, and select **Open website &
generate a code**. Choose a group, generate a code, then paste it in the app. Open
the camera workspace to add cameras. Each installation needs its own code.

```sh
.venv/bin/python scripts/build_desktop.py \
  --cloud-url http://127.0.0.1:8766 --local-demo \
  --output-dir output/desktop/groups-v0.2.0
.venv/bin/python scripts/package_macos.py \
  --app output/desktop/groups-v0.2.0/darwin-arm64/VDM.app \
  --output output/desktop/VDM-0.2.0-macos-arm64-preview.dmg \
  --self-test --window-test-report tmp/group-app-window-test.json
.venv/bin/python scripts/publish_desktop.py \
  --artifact output/desktop/VDM-0.2.0-macos-arm64-preview.dmg \
  --platform macos-arm64 --version 0.2.0-preview \
  --release-dir output/releases-local --development
```

The build check includes PyWebView, Cocoa and WebKit. Packaging signs a clean
temporary copy to avoid Finder metadata invalidating the bundle, optionally tests
bundled model inference and the native window, and creates the DMG. These previews
use ad-hoc signing and have not been notarized for public distribution.

## Group workspaces on the Mac

Credentials stay in the native OS credential store. Online lease responses include
organization/group identity, the enrolling member's email and the effective camera
allowance. The client validates on startup and periodically afterward.

```text
~/Library/Application Support/VDM/data/
  awaiting-activation/
  workspaces/org-<id>/group-<id>/
  workspaces/org-<id>/organisation/  # legacy unscoped devices
```

Switching group closes the old workspace before opening the new one. Camera settings,
uploads, incidents and learned local state use that workspace. Shipped pretrained
models are shared. Clearing activation hides the old workspace and preserves its
files. A valid activation for the same group reopens its saved workspace. Expired
or revoked group access locks its local application routes until licence recovery.

Legacy local footage remains at its old path and is not automatically assigned to
a new group. Existing unscoped cloud devices remain administrator-only. To enroll
an old device in a group, revoke it and create a group enrollment. Files are not
encrypted by this workspace feature; the OS account retains filesystem access.
Use separate OS accounts for mutually untrusted operators. Remote video, cross-device
evidence sharing and cloud camera credentials are not implemented. Only numeric
device health and aggregate counts sync to the website.

## Public deployment

Public launch needs your HTTPS domain/hosting, PostgreSQL, an SMTP provider, durable
installer storage and Apple signing credentials. Those external services have not
been provisioned. Use `deploy/cloud.env.example` for the required configuration.

```sh
python -m pip install -r requirements-cloud.txt
flask --app vdm_cloud.app:create_app init-db
gunicorn 'vdm_cloud.app:create_app()' --bind 0.0.0.0:8000 --workers 2
```

Use your host's HTTPS reverse proxy. Set `VDM_PUBLIC_URL` to the public origin used
in account emails. The cloud service needs no vision/GPU environment. Store installers
in a durable, service-owned directory shared by the cloud workers; Heroku's ephemeral
disk is unsuitable. The authenticated download endpoint checks size and SHA-256.
An external object-store adapter is not configured.

Before updating an existing cloud database, back it up and run `init-db`. This
version adds tables only: `group_licences`, `device_scopes`, `trial_grants`,
`audit_events`, `password_resets`. Existing columns are unchanged. Existing groups
need their new limits enabled through the Groups page.

Build with `--cloud-url https://YOUR-DOMAIN` and without `--local-demo`. Complete
Developer ID signing and notarization using your release identity, verify the
artifact on a clean Mac, then stage it as `published`. The preview packager produces
ad-hoc builds; it is not a production signing pipeline. See Apple's
[Developer ID guide](https://developer.apple.com/developer-id/) and
[notarization documentation](https://developer.apple.com/documentation/security/notarizing-macos-software-before-distribution).
This build targets Apple Silicon; Intel Macs require a separate native build.

Real SMTP delivery, PostgreSQL concurrency/load testing, clean-machine Keychain/camera
acceptance and production signing remain rollout steps. Local tests do not establish
that those external systems have been deployed or verified.

## Tests

`tests/test_group_workspaces.py` covers multi-user groups, tenant isolation, role
boundaries, caps, revocation including renewal retries, group health visibility,
trial replay, password reset, development download visibility, local workspace
switching and expiry. The activity log records administrative changes without
passwords, activation codes or refresh credentials. Existing account/licensing and
desktop tests remain in the full regression suite.
