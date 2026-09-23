# VDM desktop distribution — development bundles

**Current workflow:** see [group desktop workspaces](group-workspaces.md) for
administrator/operator downloads and per-group local data. The current local Mac
preview is 0.2.1.
Production configurations still hide development artifacts.

## 0.2.1 local preview

The Apple Silicon app was rebuilt from the current source on 2026-09-22. It includes
ZipDepth ONNX, matched-frame depth support for the five-second fight confirmation
window, the “Checking interaction” indicator and “Possible fight / review” label.
The bundle and installed app report both version and build as 0.2.1. This rebuild
does not introduce a trained action-recognition model or resolve guitar-strike
recognition; automated gate checks do not measure real-footage detection accuracy.

Artifact: `output/releases-local/VDM-0.2.1-macos-arm64-preview.dmg`.
The local download catalogue marks it as development only. Frozen model inference,
confirmation-policy presence, UI routes, native-window launch and ad-hoc signature
verification passed. The installed `/Applications/VDM.app` was replaced with the
verified DMG copy; the previous app was retained as a hidden backup in Applications.
Existing Application Support data and Keychain credentials were not modified by
the installer. Verification reports are in `tmp/installed-app-0.2.1.json` and
`tmp/native-window-0.2.1.json`.

This remains an ad-hoc-signed local preview requiring the management server at
`http://127.0.0.1:8766`; Developer ID signing and Apple notarization remain pending.

Implemented: native build script, managed loopback launcher, curated model asset
selection, per-user storage, authenticated member download page, integrity
checks and explicit publication tool. These are development builds. macOS now has a native .app and drag-to-Applications
DMG; Developer ID signing and notarization remain pending. No public download is advertised until a release is
explicitly staged. Existing cloud accounts and local evidence are not packaged.

## Build on each target platform

Use a clean Python 3.11 virtual environment on macOS or Windows. Build separately
for Apple Silicon, Intel Mac and Windows x64; PyInstaller does not cross-compile.

    python -m pip install -r requirements-desktop-build.txt
    python scripts/build_desktop.py --cloud-url https://YOUR-HEROKU-APP --check
    python scripts/build_desktop.py --cloud-url https://YOUR-HEROKU-APP

For a local-only demonstration with the cloud server running on the same device:

    python scripts/build_desktop.py --cloud-url http://127.0.0.1:8766 --local-demo

Outputs: output/desktop/<native-system>-<architecture>/VDM. Keep the entire folder;
the executable alone is not enough. Launch VDM (VDM.exe on Windows). Windows currently has a console window and browser UI. macOS uses VDM.app with a
native window; package it into a DMG using the command below. Neither platform
has a background service or managed auto-update yet.

Models must already exist under models/; use the documented model download scripts
before building. Only the curated model assets are included, not the
entire repository or data directory. Include applicable third-party/model notices and
verify redistribution permissions before distributing. PyInstaller collects native
runtime dependencies, templates, static files, ONNX Runtime and ffmpeg assets.
ZipDepth ships as its ONNX graph, checksum manifest and MIT notice. Existing bundles
need rebuilding; source edits do not update already built apps.
The 0.2.0 Apple Silicon preview passed bundled inference and native-window smoke
tests. Camera and actual Keychain activation still require clean-machine acceptance.

The launcher binds an OS-selected free port on 127.0.0.1. A per-user OS file lock
prevents two updated desktop instances from sharing credentials/databases; another
launch reopens the running instance. The source server may still use port 8765. It sets managed licensing from its bundled
config and uses:

- macOS: ~/Library/Application Support/VDM
- Windows: %LOCALAPPDATA%/VDM

Models are initially copied there because runtime caches must be writable. Existing
files are preserved. There is no automatic model upgrade/migration in this version.
Startup copy needs extra disk space approximately equal to the shipped model assets.
Never embed customer activation codes, cloud secrets or organisation credentials.
The launcher only embeds the cloud origin and whether loopback demo mode is enabled.

## Native release gates

Before publication, test on a clean target machine without Python installed:

1. Extract and launch; verify templates, documentation and local browser interface.
2. Activate; verify actual OS credential-store write/read and restart renewal.
3. Process an uploaded clip and camera with pose, depth, object/threat workers.
4. Verify ffmpeg evidence clips, review, training and local metadata sync.
5. Test offline expiry, revocation, restart, shutdown and second-instance rejection.
6. Verify no user data or credentials in the archive; include licences/notices.
7. Sign using the release owner's identity; notarize macOS distribution and verify
   Windows signature as appropriate for the chosen installer. Signing credentials
   are not available to this project yet. Do not describe unsigned bundles as signed.
8. Archive the complete native bundle with a platform/version-specific filename.

No Windows build or native end-to-end verification has been performed here.

## Stage a tested download

    python scripts/publish_desktop.py --artifact PATH-TO-VERIFIED.zip --platform macos-arm64 --version 0.1.0 --release-dir output/releases --verified

Set VDM_RELEASE_DIR to the absolute release directory when starting the cloud app.
Signed-in administrators and operators can use /downloads; anonymous users cannot
retrieve artifacts. Production settings hide development preview artifacts. The server checks filenames, size and SHA-256 before
serving a release. The directory and manifest must remain service-owned and immutable
except during controlled publication. Hashes detect changes; they do not replace
code signing. An empty/missing manifest shows a clear 'being prepared' state.

This directory-backed delivery is for local/staging servers. Heroku's temporary disk
is not durable artifact hosting. Large inference bundles belong in external artifact
storage with authenticated, short-lived download links. That provider integration,
actual upload and a permanent HTTPS activation domain are not configured yet.

References: https://pyinstaller.org/en/latest/usage.html

## Latest local verification

Download authorisation/integrity tests and launcher asset-preservation tests passed.
The macOS build was attempted but stopped in PyInstaller's NumPy hook because the
existing .venv exposes an invalid/missing NumPy distribution version. No runnable
archive was generated or published. The build preflight now detects this condition.
Use a fresh environment for the next build; the application's current environment
has not been repaired or replaced. The build log is tmp/desktop-build.log.

## Clean macOS build result

A new isolated environment at tmp/desktop-venv successfully built the Apple Silicon
local-demo console bundle at output/desktop/darwin-arm64/VDM. Its executable passed
`VDM --self-test`: real inference on synthetic pixels using pose, ZipDepth depth, object
and threat models; ResNet encoder loading; ffmpeg binary presence; native keyring
backend import; Flask dashboard, activation and static asset routes. This is a runtime
smoke test, not detection-quality validation. No live camera, actual Keychain roundtrip,
cloud activation, signed/notarized distribution or Windows execution was tested.

The bundle is configured for http://127.0.0.1:8766, so run the local cloud server on
the same machine. It will not work with a remote customer until rebuilt with the
actual HTTPS cloud origin. Do not publish it as a production release.

Resolved dependencies are recorded in desktop/build-macos-arm64.lock.txt. The old
.venv was left intact. Build caches now live under tmp/desktop-build to avoid writing
to user-library cache directories. The clean build log is tmp/desktop-build-clean.log;
the frozen self-test log is tmp/desktop-self-test.log.

## Port-conflict fix

Desktop builds now use an automatically assigned loopback port and open the exact
URL in the browser. Do not bookmark port 8765 for the desktop build; relaunch VDM to
reopen it. Cloud activation still uses port 8766 in this local-demo build. The old
extracted output/desktop/VDM directory is not automatically overwritten: extract
the rebuilt ZIP into a fresh folder to avoid retaining the old executable.

## Native macOS application

The macOS build now produces VDM.app with a Dock icon and a native WebKit window.
No Terminal or separate browser is needed to launch this application. Closing the
window shuts down the local server and processing; opening again restores saved
state and revalidates the licence. Local data remains in Application Support/VDM.
Windows retains its existing console build path until native Windows testing.

Build the app with build_desktop.py, then create the drag-to-Applications installer:

    python scripts/package_macos.py --app output/desktop/darwin-arm64/VDM.app --output output/desktop/VDM-macos-arm64.dmg

Open the DMG, drag VDM.app to Applications, and launch it from Applications/Spotlight.
The local-demo build still depends on run_cloud.py at 127.0.0.1:8766. It is not a
self-hosted production licensing service. The bundled app is ad-hoc signed only,
not Developer ID signed/notarized; macOS download security may block it. A public
release requires the owner's Apple Developer identity and notarization workflow.
Do not disable Gatekeeper globally. No signing identity is invented or included.

### If macOS blocks the development preview

The current ad-hoc-signed preview has not been notarized by Apple, so macOS may
show **“Apple could not verify ‘VDM’ is free of malware.”** Only continue if you
trust this locally built preview and its download source.

1. Open the DMG, drag **VDM** to **Applications**, and try opening it from Applications.
2. Open **System Settings → Privacy & Security**. Find the message about VDM and
   choose **Open Anyway**.
3. Authenticate if prompted, then confirm **Open**.

This creates an exception for this app; it does not notarize the preview. It is
not the customer release process. Customer releases require Developer ID signing,
Apple notarization, and clean-machine verification. See
[Apple’s instructions for opening an app from an unidentified developer](https://support.apple.com/en-us/102445).

Native-window smoke test (requires a graphical session and local socket permission):

    output/desktop/darwin-arm64/VDM.app/Contents/MacOS/VDM --window-test tmp/native-window-result.json

It opens a test window, checks a local rendered DOM value, writes a JSON result and
closes. It does not activate a device or access camera/keychain/video data.
