"""Exercise tool setup against local Git repositories without hardware or network."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from src.common import UserError
from src.setup import usbboot


class UsbbootSetupTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.repo = self.root / 'workspace'
        self.repo.mkdir()
        environment = patch.dict(os.environ, {
            'GIT_ALLOW_PROTOCOL': 'file', 'GIT_AUTHOR_NAME': 'Setup test',
            'GIT_AUTHOR_EMAIL': 'test@example.invalid', 'GIT_COMMITTER_NAME': 'Setup test',
            'GIT_COMMITTER_EMAIL': 'test@example.invalid',
        })
        environment.start()
        self.addCleanup(environment.stop)
        self.eeprom = self.root / 'upstream-eeprom'
        self.git('init', '--quiet', str(self.eeprom))
        (self.eeprom / 'fixture').write_text('pinned EEPROM tools\n')
        self.commit(self.eeprom)
        self.source = self.root / 'upstream-usbboot'
        self.git('init', '--quiet', str(self.source))
        (self.source / '.gitignore').write_text('rpiboot\n')
        (self.source / 'Makefile').write_text('rpiboot: Makefile\n\tprintf tool > rpiboot\n\tchmod +x rpiboot\n')
        self.git('-C', str(self.source), 'submodule', 'add', str(self.eeprom), 'rpi-eeprom')
        self.commit(self.source)
        self.options = dict(url=str(self.source), revision=self.head(self.source),
                            eeprom_revision=self.head(self.eeprom))
        self.target = self.repo / 'local/usbboot'

    def git(self, *args):
        return subprocess.check_output(['git', *args], text=True, stderr=subprocess.DEVNULL).strip()

    def head(self, path):
        return self.git('-C', str(path), 'rev-parse', 'HEAD')

    def commit(self, path):
        self.git('-C', str(path), 'add', '.')
        self.git('-C', str(path), 'commit', '--quiet', '-m', 'fixture')

    def install(self):
        with patch.object(usbboot, 'prerequisites'):
            usbboot.install(self.repo, **self.options)

    def test_download_build_and_offline_reuse(self):
        self.install()
        self.assertTrue(os.access(self.target / 'rpiboot', os.X_OK))
        self.assertEqual(self.head(self.target), self.options['revision'])
        self.assertEqual(self.head(self.target / 'rpi-eeprom'), self.options['eeprom_revision'])
        self.options['url'] = str(self.root / 'unavailable')
        self.source.rename(self.root / 'hidden-upstream')
        self.eeprom.rename(self.root / 'hidden-eeprom')
        self.install()
        (self.target / 'rpiboot').unlink()
        self.install()
        self.assertTrue((self.target / 'rpiboot').is_file())

    def test_edited_checkout_is_preserved(self):
        self.install()
        path = self.target / 'Makefile'
        path.write_text('local edits\n')
        with self.assertRaisesRegex(UserError, 'local changes'):
            self.install()
        self.assertEqual(path.read_text(), 'local edits\n')

    def test_wrong_pin_is_rejected_without_replacing_checkout(self):
        self.install()
        self.options['revision'] = '0' * 40
        with self.assertRaisesRegex(UserError, 'expected commit'):
            self.install()
        self.assertEqual(self.head(self.target), self.head(self.source))

    def test_failed_build_leaves_no_partial_install(self):
        (self.source / 'Makefile').write_text('rpiboot:\n\tfalse\n')
        self.commit(self.source)
        self.options['revision'] = self.head(self.source)
        with self.assertRaises(subprocess.CalledProcessError):
            self.install()
        self.assertFalse(self.target.exists())
        self.assertEqual([p for p in (self.repo / 'local').glob('.usbboot-*') if p.is_dir()], [])
