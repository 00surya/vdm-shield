"""Create a drag-to-Applications DMG from the native PyInstaller app."""
import argparse
from pathlib import Path
import platform
import shutil
import subprocess
import tempfile


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--app', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--self-test', action='store_true', help='Run frozen inference and UI-route checks on the staged app')
    parser.add_argument('--window-test-report', type=Path, help='Run a native window check and save its JSON report')
    args = parser.parse_args()
    if platform.system() != 'Darwin' or not (args.app / 'Contents' / 'MacOS' / 'VDM').is_file():
        parser.error('Provide a built VDM.app on macOS.')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='vdm-dmg-', dir='/private/tmp') as temporary:
        stage = Path(temporary)
        shutil.copytree(args.app, stage / 'VDM.app', symlinks=True, copy_function=shutil.copy)
        subprocess.run(['xattr', '-cr', str(stage / 'VDM.app')], check=True)
        subprocess.run(['codesign', '--force', '--deep', '--sign', '-', str(stage / 'VDM.app')], check=True)
        subprocess.run(['codesign', '--verify', '--deep', '--strict', str(stage / 'VDM.app')], check=True)
        executable = stage / 'VDM.app/Contents/MacOS/VDM'
        if args.self_test:
            subprocess.run([str(executable), '--self-test'], check=True)
        if args.window_test_report:
            subprocess.run([str(executable), '--window-test', str(args.window_test_report.resolve())], check=True)
        (stage / 'Applications').symlink_to('/Applications', target_is_directory=True)
        (stage / 'READ ME.txt').write_text(
            'VDM — macOS local demonstration\n\n'
            'Drag VDM.app to Applications, then open VDM from Applications or Spotlight.\n'
            'The app opens its own window. Close that window to stop processing.\n'
            'This demo requires run_cloud.py on the same Mac for activation.\n'
            'No public cloud service is bundled. Video and evidence stay on this device.\n'
            'This development build is not Developer ID signed or notarized.\n'
            'macOS may say Apple could not verify VDM is free of malware.\n\n'
            'If you trust this locally built preview and choose to test it:\n'
            '1. Drag VDM.app to Applications and try opening it there.\n'
            '2. Open System Settings > Privacy & Security.\n'
            '3. Find the message about VDM and choose Open Anyway.\n'
            '4. Authenticate if asked, then confirm Open.\n'
            'This creates an exception for this app; it does not notarize it.\n'
            'Do not disable Gatekeeper globally. Customer releases need Developer ID signing and Apple notarization.\n'
            'Apple guidance: https://support.apple.com/en-us/102445\n')
        subprocess.run(['hdiutil', 'create', '-volname', 'Install VDM', '-srcfolder', str(stage),
                        '-format', 'UDZO', '-ov', str(args.output.resolve())], check=True)


if __name__ == '__main__':
    main()
