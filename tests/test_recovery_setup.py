"""Recreate RAM payloads from downloads, without commissioning artifacts or hardware."""
import io
from pathlib import Path
import tarfile
import tempfile
import unittest

from src.common import UserError
from src.setup import recovery
from src.wired.storage import payload_hash


class RecoverySetupTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.repo = Path(temporary.name)
        source = self.repo / 'package/polis-wired-control/recovery'
        source.mkdir(parents=True)
        self.upstream = self.repo / 'local/usbboot/mass-storage-gadget64'
        self.upstream.mkdir(parents=True)
        (source / 'config.txt').write_text('boot_ramdisk=1\nuart_2ndstage=1\n')
        (self.upstream / 'boot.img').write_bytes(b'RAM environment')
        archive = self.upstream.parent / 'bootfiles.bin'
        with tarfile.open(archive, 'w') as stream:
            member = tarfile.TarInfo('2711/bootcode4.bin')
            member.size = 6
            stream.addfile(member, io.BytesIO(b'loader'))
        (self.upstream / 'bootfiles.bin').symlink_to(archive)
        (self.upstream / 'config.txt').write_bytes((source / 'config.txt').read_bytes())
        self.expected = payload_hash(self.upstream)
        (source / 'payload.sha256').write_text(self.expected + '\n')
        self.target = self.repo / 'local/polis-wired-tools/gadget'

    def test_fresh_setup_and_reuse_without_old_recovery_directory(self):
        recovery.install(self.repo)
        self.assertEqual(payload_hash(self.target), self.expected)
        self.assertFalse((self.target / 'bootfiles.bin').is_symlink())
        self.assertFalse((self.repo / 'local/polis-recovery').exists())
        self.upstream.rename(self.upstream.with_name('unavailable'))
        recovery.install(self.repo)
        self.assertEqual(payload_hash(self.target), self.expected)

    def test_changed_download_is_not_published(self):
        (self.upstream / 'boot.img').write_bytes(b'changed upstream')
        with self.assertRaisesRegex(UserError, 'hash mismatch'):
            recovery.install(self.repo)
        self.assertFalse(self.target.exists())
        self.assertEqual(list(self.target.parent.glob('.gadget-*')), [])

    def test_edited_installed_payload_is_preserved(self):
        recovery.install(self.repo)
        (self.target / 'config.txt').write_text('local edits')
        with self.assertRaisesRegex(UserError, 'hash mismatch'):
            recovery.install(self.repo)
        self.assertEqual((self.target / 'config.txt').read_text(), 'local edits')
