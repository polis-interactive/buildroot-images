"""Retiring the userspace worker preserves standard device rules and other apps."""
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    'install_controller_usb', REPO / 'board/rpi4/install-controller-usb.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class ControllerUsbTests(unittest.TestCase):
    def test_retirement_is_scoped_and_idempotent(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary)
            for name in installer.LEGACY_FILES:
                path = target / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('retired')
            assets = target / 'usr/share/polis-graphics-usb'
            assets.mkdir(parents=True)
            (assets / 'manifest.json').write_text('{}')
            other = target / 'usr/bin/hdmi-passthru'
            other.write_text('keep')
            config = target / 'etc/mdev.conf'
            standard = ('# Standard device rules\n'
                        'snd/.* root:audio 660\n'
                        'input/.* root:input 660\n'
                        'ttyACM[0-9]+ root:root 660\n'
                        '$MODALIAS=.* root:root 660 @modprobe "$MODALIAS"\n')
            config.write_text(installer.LEGACY_RULE + '\n' + standard
                              + '  ' + installer.LEGACY_RULE + '  \n')
            # Buildroot executes post-build scripts directly, requiring +x.
            subprocess.run([str(REPO / 'board/rpi4/install-controller-usb.py'),
                            str(target)], check=True)
            installer.install(target)
            installer.install(target)
            self.assertEqual(config.read_text(), standard)
            self.assertEqual(other.read_text(), 'keep')
            self.assertFalse(assets.exists())
            for name in installer.LEGACY_FILES:
                self.assertFalse((target / name).exists())

    def test_fresh_target_needs_no_legacy_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer.install(Path(temporary))
            self.assertEqual(list(Path(temporary).iterdir()), [])

    def test_retired_symlinks_do_not_delete_their_destinations(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / 'target'
            keep = root / 'keep'
            keep.mkdir()
            payload = keep / 'payload'
            payload.write_text('keep')
            for name in installer.LEGACY_FILES:
                path = target / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.symlink_to(payload)
            assets = target / 'usr/share/polis-graphics-usb'
            assets.parent.mkdir(parents=True)
            assets.symlink_to(keep, target_is_directory=True)
            installer.install(target)
            self.assertEqual(payload.read_text(), 'keep')
            self.assertFalse(assets.is_symlink())
            for name in installer.LEGACY_FILES:
                self.assertFalse((target / name).is_symlink())
