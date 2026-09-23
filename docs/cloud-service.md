# VDM cloud service — steps 1–5

**Current workflow:** see [organization and group workspaces](group-workspaces.md).
Group-scoped activation, operator downloads/health, trials, password recovery and
an administrative activity log supersede the earlier staged limitations below.

This separate Flask service manages accounts, groups, device licensing, downloads
and numeric device health. It does not import the video engine or accept footage.
The source video application can still be run using run.py / start.command.

## Try locally

    .venv/bin/pip install -r requirements-cloud.txt
    .venv/bin/python run_cloud.py

Open http://127.0.0.1:8766. Create an organisation and select **Continue verification**
on the next page, choose your sign-in password and sign in. The local launcher
shows the verification step in the browser instead of sending real email; it also
prints links in the terminal. Existing unverified accounts can sign in with their
original password to return to verification. This development mode must never be
exposed publicly. SQLite data and a development secret live under data/cloud-dev
(ignored by version control). Production keeps browser email previews disabled
and uses SMTP, with a resend action on the verification page. Email ownership is
not proof of legal organisation ownership.

## Heroku preparation

Use [the deployment guide](heroku-deployment.md) to prepare an isolated cloud-only
bundle. The Procfile now initializes missing database tables in the release phase.

The root Procfile and requirements.txt deploy the cloud service only. Configure:

- VDM_CLOUD_SECRET: a randomly generated secret, at least 32 characters.
- DATABASE_URL: the managed PostgreSQL connection URL.
- VDM_PUBLIC_URL: the actual HTTPS website URL, without a trailing slash.
- SMTP_HOST, SMTP_PORT (587), SMTP_USER, SMTP_PASSWORD, MAIL_FROM: verified mail service.

Create the initial database once using:

    flask --app vdm_cloud.app:create_app init-db

This command creates missing tables; it does not migrate existing schemas. Add
versioned migrations before changing a deployed schema. Do not use local SQLite
on Heroku. Do not check secrets into source control. The Heroku deployment is now
live; see [deployment status and details](heroku-deployment.md). Email provider
setup is still pending.

## Implemented and tested

- Organisation signup, hashed passwords, timed email verification.
- Verification chooses a fresh password, preventing preregistration takeover.
- Administrator sign-in and organisation-scoped dashboard.
- CSRF protection, secure production cookies, expiring server-backed sessions.
- Logout revokes the server session, including replay of an older browser cookie.
- Persistent per-email signup/login limits, generic invalid-login responses.
- Administrator-created groups and expiring operator invitations.
- Single-use acceptance, invitation revocation and failed-delivery status.
- Operators choose passwords; administrators cannot view them.
- Administrator-only group assignment, suspension and restoration.
- Suspension revokes existing sessions and prevents new sign-in.
- Cross-organisation requests are rejected server-side.
- No cloud routes for video, frames, camera credentials or local models.

## Remaining release work

1. Account recovery and administrator MFA.
2. Subscription billing integration (licences are currently provisioned by service-owner CLI).
3. Native credential-store roundtrip and Windows desktop validation; signed installers.
4. Group-scoped insights, optional time-series retention and sync audit records.
5. macOS/Windows packaging, signing and platform tests.
6. Operational hardening: MFA, audit trail, database backups, migrations, trusted
   ingress/IP rate limits, email abuse controls and PostgreSQL integration tests.

Per-email limits do not prevent attackers sending requests to many addresses;
this initial service needs ingress abuse protection before public registration.
Downloads and billing are intentionally labelled
as planned in the dashboard. No claims of production readiness are made.

## Try people management

Restart run_cloud.py, sign in as an administrator and select **Manage people**.
Create a group, then invite a new email address into it. The development launcher
prints the invitation link in the terminal. Open it in a private browser window,
choose a password and sign in as that operator. Operators see only their own group
names on the dashboard; they cannot open administrator people management.

Back in the administrator window, change group checkboxes and select **Save group
access**. **Suspend access** immediately denies the operator's next request and
revokes saved sessions. **Restore access** permits a fresh sign-in. Pending
invitations can be revoked; expired or failed invitations require a new invitation.
An email address already registered cannot be moved to another organisation via
this flow. Each account currently belongs to one organisation.

The new groups, memberships and invitations tables are additive: restarting the
local launcher creates them while preserving existing accounts. For an existing
cloud database, run the documented init-db command before starting the updated
service. No existing columns change in this step.

Groups now scope device enrollment and health visibility. Operators share the same
operator role; custom roles and additional administrators are not implemented.
Password recovery uses single-use, expiring email links and revokes website sessions.

Run account and people security checks:

    .venv/bin/python -m pytest tests/test_cloud_accounts.py tests/test_cloud_people.py -q

## Device activation and licensing (step 3)

Restart the local service and open **Manage devices**. The page shows your organisation ID.
Provision a local seven-day demo entitlement from another terminal:

    .venv/bin/python run_cloud.py --grant-demo ORGANISATION_ID

Reload the devices page. Generate an activation code (shown once, valid one hour).
It reserves one device slot. Revoke unused devices to free slots. Codes and refresh
credentials are stored only as hashes. Keep the displayed code private: it authorises
a device to claim the slot. No hardware attestation is provided.

Production service owners provision entitlements using the configured cloud environment:

    flask --app vdm_cloud.app:create_app grant-licence --organisation-id 1 --days 30 --devices 2 --cameras 4

This REPLACES expiry with now plus the selected days; it is not a payment integration.
Tenant administrators cannot grant themselves a subscription or change its limits.
The camera allowance is per device, not an inference performance promise. Lowering
device allowance requires revoking surplus reservations/devices first.

### Device API contract

All calls are POST with application/json, without browser Origin headers. Only these
three endpoints bypass form CSRF; they authenticate using activation/refresh/lease
secrets, never browser sessions. HTTPS is required for deployed clients.

- `/api/device/activate`: `{ "activation_code": "..." }`. Single-use code, returns
  refresh_token, lease, device_id, camera_limit, licence_expires, valid_until and
  check_again_in_seconds. No video, frames or hardware serial numbers are accepted.
- `/api/device/renew`: `{}` with `Authorization: Bearer <refresh_token>`. Rotates the
  token on every success. Old tokens and their leases stop working. Save the returned
  token securely before another request. Concurrent renewals must be avoided.
- `/api/device/check`: `{ "lease": "..." }`. Checks the signed lease AND live database
  state; rejects expired subscriptions, revoked devices and superseded credentials.

Leases last at most five minutes, capped at subscription expiry, with a suggested
renewal interval of two minutes. These are server-validated signatures: NEVER ship
VDM_CLOUD_SECRET to a client. The local client now enforces source processing when VDM_CLOUD_URL is configured
and stores credentials using the native OS backend. See step 4 below.
Revocation is immediate on the next server check; any future offline cached client
permission must stop by lease expiry. No evidence is deleted by licensing actions.

Renewal supports a persisted request_id (20–128 characters). For ten minutes,
repeating the same old credential plus request_id returns the same replacement
credential, provided the device remains active and has not renewed again. Requests
without request_id retain single-use rotation behaviour. Retry receipts store only
hashes. A retry outside the window requires reactivation.
API ingress abuse limits, device proof-of-possession, billing and PostgreSQL
concurrency validation remain production work. Administrative audit records are implemented. Activation codes are
bearer secrets and do not alone establish the identity of the person using a device.

New entitlement/device tables are additive. The local launcher creates them; run
init-db before deploying this version against an existing cloud database.

## Connect the desktop client (step 4)

Install desktop storage dependencies:

    .venv/bin/pip install -e '.[desktop]'

Start the cloud service first. Restarting it creates the new renewal_receipts table
locally; a deployed database needs init-db before using this client version.
Start the local video app in managed mode for a local demonstration:

    VDM_REQUIRE_LICENCE=1 VDM_CLOUD_URL=http://127.0.0.1:8766 VDM_CLOUD_ALLOW_LOCAL=1 .venv/bin/python run.py

Open http://127.0.0.1:8765/activation, then enter the code from the cloud dashboard.
For deployment, use VDM_CLOUD_URL=https://your-server and omit VDM_CLOUD_ALLOW_LOCAL.
HTTP is only accepted for explicitly enabled loopback demos. Redirects are rejected
to avoid forwarding credentials to another host. The server URL is configured by
the launcher, not by arbitrary browser input.

Credential storage is macOS Keychain or Windows Credential Manager through explicit
native keyring backends. No plaintext fallback exists. OS denial/locked storage
produces an actionable error and leaves processing locked. Installation identity is
scoped by cloud origin and data-directory path; moving the data directory creates a
new storage identity. Refresh tokens and pending renewal request IDs are stored
there; the browser never receives them. Native store writes and Windows still need
manual platform verification. Tests use an in-memory test double.

A background thread renews at most every two minutes and retries transient failures
after 15 seconds. Permission uses a monotonic deadline (at most five minutes from
request start), capped by subscription expiry. No persisted lease is trusted on
restart: internet validation is required. Revocation is noticed at the next renewal;
HTTP 401/403 immediately clears local permission. During an outage, the current
lease can finish, then processing stops at the next processing-loop boundary after
any blocking decode/inference operation returns. This is not hard real-time shutdown.

New sources and restarts are gated. Saved enabled cameras do not start before
validation and reconnect after recovery. Uploads require manual restart. This first
client conservatively counts ALL source cards (including uploaded videos) against
min(camera allowance, the app's four-source cap); remove unused cards to free slots.
New training jobs are blocked without a valid licence, but an already running
training job is allowed to finish. Evidence browsing/review remains available and
no evidence is deleted by a licence failure.

Removing local activation stops processing but does not revoke the cloud device;
revoke that device on the website to free its slot before generating a replacement.
Activation response loss or an OS storage failure after consuming a code still
requires revocation/re-activation; renewal response loss has the retry recovery
above. Only one local app process may use a given credential-store identity.

Without VDM_CLOUD_URL, the existing source checkout stays in clearly labelled
unlicensed development mode. VDM_REQUIRE_LICENCE=1 fails startup if the URL is missing.
This is configuration-based prototype enforcement, not protection against a customer
editing source code. Signed packaging and protected production configuration remain
future work. No cloud administrator browser session is created by activation, and
no video, thumbnails, camera URLs or credentials are uploaded.

    .venv/bin/python -m pytest tests/test_desktop_licensing.py tests/test_cloud_licensing.py tests/test_api.py tests/test_appliance.py -q

## Device health and analytics sync (step 5)

Restart the cloud and local apps. The local launcher creates the new
`device_snapshots` table. For an existing deployed database, run init-db first.
Once activated and validated, the managed local app sends one latest snapshot
approximately every 60 seconds. Open **View device insights** on the organisation
dashboard and select **Refresh snapshots** to see current values. No manual upload
is required. Activation & licence on the local app shows sync success/failure.

The entire accepted JSON schema contains these numeric fields, with no extras:

- schema (currently 1)
- source_count, running_sources, stale_sources
- analysis_fps (sum across running sources)
- free_disk_gb (decimal GB on the local data drive)
- alerts_24h, confirmed_24h, false_positive_24h, unreviewed_24h

Review totals refer to event records created in the last rolling 24 hours using the
local device's clock and live mode. They include uploaded video analysis, exclude
synthetic demos, and are not measures of recall/accuracy or unique real incidents.
Only review-count SQL aggregates are read; individual event records are not uploaded.
No camera names, URLs, filenames, credentials, event text, thumbnails, coordinates,
video, frames, model weights or hardware identifiers are sent. Displayed device names
are the administrator-entered names already stored by the website.

The device posts to `/api/device/metadata` using its current bearer credential.
The server derives the organisation from that credential, validates licence status
and rejects unknown fields, booleans, invalid counter relationships, NaN/infinity,
strings and media. Browser cookies cannot authenticate this endpoint. The server
serializes ingestion with credential renewal/revocation and allows one snapshot per
15 seconds per device. This is not a replacement for production ingress limits.

One row per device is replaced on each successful sync; there is no growing history
or alert replay queue. Duplicate transport retries cannot double-count events because
values replace rather than increment. An interrupted credential renewal must finish
before sync can proceed. Sync and renewal use the same local operation lock to avoid
sending a superseded token. Sync failures retry automatically without changing the
licence deadline. Capturing/processing threads do not wait for the sync network call.

Snapshots over three minutes old are labelled stale using cloud receipt time. Recent
communication does not prove a healthy camera; inspect delayed-source counts. Revoked
devices retain their last snapshot, labelled stale. No aggregate combines snapshots
from different times into a purported live total. Administrator access only: operators
receive 403; group-wide views will need explicit device/group assignment first.

Test coverage includes the full local collector→validator boundary, authenticated
cloud ingest, forbidden media fields, organisation separation, operator restrictions,
revocation, expired credentials, stale UI, replacement semantics and network failure.
Manual cloud deployment and live Keychain-backed end-to-end sync remain unverified.

## Desktop downloads (step 6, release preparation)

The administrator dashboard now links to /downloads. Native build and publication
scripts are documented in [desktop-distribution.md](desktop-distribution.md).
No platform download is shown until a verified archive is explicitly staged with
its checksum. VDM_RELEASE_DIR configures local/staging artifact storage; Heroku
production artifact hosting still requires an external provider integration.
