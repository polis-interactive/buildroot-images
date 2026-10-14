"""Host-side Wi-Fi image inputs, staging and archive extraction; no target builds."""
import importlib.util
import io
import json
import os
import subprocess
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import tarfile

from src.common import UserError
from src.profiles import wifi
from src.profiles.image import assemble, recipe

REPO = Path(__file__).resolve().parents[1]
PACKAGE = REPO / 'package/polis-wifi-setupd'
spec = importlib.util.spec_from_file_location('wifi_web', PACKAGE / 'extract-web.py')
web = importlib.util.module_from_spec(spec)
spec.loader.exec_module(web)


class PasswordTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.enterContext(patch.dict(os.environ, {}, clear=True))

    def test_env_overrides_local_file_and_missing_input_is_actionable(self):
        self.assertEqual(wifi.password_input(self.root), '')
        with self.assertRaisesRegex(UserError, 'POLIS_WIFI_SETUP_PASSWORD'):
            wifi.require_password('')
        local = self.root / '.local/wifi-setupd/password'
        local.parent.mkdir(parents=True)
        local.write_text('local-password123\n')
        self.assertEqual(wifi.password_input(self.root), 'local-password123')
        with patch.dict(os.environ, POLIS_WIFI_SETUP_PASSWORD='environment123'):
            self.assertEqual(wifi.password_input(self.root), 'environment123')
        with patch.dict(os.environ, POLIS_WIFI_SETUP_PASSWORD=''):
            with self.assertRaises(UserError):
                wifi.password_input(self.root)

    def test_password_validation_never_echoes_invalid_secret(self):
        for value in ('short', 'space in password', 'x' * 64, 'abcdefgh\x00'):
            with self.assertRaises(UserError) as error:
                wifi.validate_password(value)
            self.assertNotIn(value, str(error.exception))

    def test_staging_writes_matching_pair_and_can_replace_default(self):
        router = self.root / 'var-lib/iwd/home.psk'
        router.parent.mkdir(parents=True)
        router.write_text('saved-router')
        for password in ('image-default123', 'future-device456'):
            wifi.write_credentials(self.root, password)
            self.assertEqual((self.root / 'wifi-setupd/device-key').read_text(), password + '\n')
            ssid = (self.root / 'wifi-setupd/ssid').read_text().strip()
            ap = self.root / f'var-lib/iwd/ap/{ssid}.ap'
            self.assertIn('Passphrase=' + password + '\n', ap.read_text())
            self.assertNotIn('PreSharedKey=', ap.read_text())
            self.assertIn('DisableHT=true', ap.read_text())
            for field in ('Gateway', 'DNSList'):
                self.assertIn(field + '=192.168.16.1', ap.read_text())
            self.assertEqual(ap.stat().st_mode & 0o777, 0o600)
            self.assertEqual((self.root / 'wifi-setupd/device-key').stat().st_mode & 0o777, 0o600)
            self.assertEqual((self.root / 'wifi-setupd').stat().st_mode & 0o777, 0o700)
        self.assertEqual(router.read_text(), 'saved-router')

    def test_iwd_string_escaping_preserves_backslashes_and_punctuation(self):
        password = r'abc\n\s\t\r\#;=%123\\'
        wifi.write_credentials(self.root, password)
        self.assertEqual((self.root / 'wifi-setupd/device-key').read_text(), password + '\n')
        ap = (self.root / 'var-lib/iwd/ap/Polis-Setup.ap').read_text()
        self.assertIn('Passphrase=abc\\\\n\\\\s\\\\t\\\\r\\\\#;=%123\\\\\\\\\n', ap)

    def test_image_assembly_seeds_data_and_assigns_root_ownership_without_runtime_code(self):
        output = self.root
        for name in ('profile', 'images', 'build', 'host', 'target/var/lib/dbus'):
            (output / name).mkdir(parents=True, exist_ok=True)
        (output / 'target/var/lib/dbus/machine-id').write_text('fixture')
        plan = dict(lz4=False, premerge=False, cutdown=False, ramdisk=False,
                    reproducible=True, source_date_epoch=1234567890,
                    wifi_setup=True, development=False, eeprom={}, profile='wifi', fingerprint='test')
        (output / 'profile/image-plan.json').write_text(json.dumps(plan))
        (output / 'profile/wifi-password').write_text('image-default123')
        env = dict(BASE_DIR=str(output), BR2_EXTERNAL_POLIS_PATH=str(REPO),
                   BINARIES_DIR=str(output / 'images'), HOST_DIR=str(output / 'host'),
                   BUILD_DIR=str(output / 'build'))

        def inspect(args, **kwargs):
            self.assertEqual(args[0], str(output / 'host/bin/fakeroot'))
            self.assertIn('chown -h -R 0:0 "$1"', args[4])
            root = Path(args[args.index('--rootpath') + 1])
            self.assertEqual((root / 'data/wifi-setupd/device-key').read_text(), 'image-default123\n')
            self.assertEqual((root / 'data/var-lib/dbus/machine-id').read_text(), 'fixture')
            self.assertEqual((root / 'data/wifi-setupd/device-key').stat().st_mtime, 1234567890)
            self.assertIn('mountpoint = "/data"', (output / 'profile/genimage.cfg').read_text())
            self.assertNotIn('image-default123', str(args))

        with patch('src.profiles.image.subprocess.run', side_effect=inspect) as run:
            assemble(env)
            run.assert_called_once()
        plan['wifi_setup'] = False
        self.assertIn('empty = true', recipe(output / 'images', plan)[0])


class InitScriptTests(unittest.TestCase):
    def test_init_launches_daemon_directly_and_leaves_credentials_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            wifi.write_credentials(root, 'fixture-password123')
            before = {p: p.read_bytes() for p in root.rglob('*') if p.is_file()}
            script = root / 'S85wifi-setupd'
            script.write_text((PACKAGE / 'S85wifi-setupd').read_text()
                              .replace('state=/data/wifi-setupd', f'state={root}/wifi-setupd')
                              .replace('pidfile=/run/wifi-setupd.pid', f'pidfile={root}/pid')
                              .replace('/var/lib/iwd/ap/', f'{root}/var-lib/iwd/ap/'))
            commands = root / 'bin'
            commands.mkdir()
            for name, body in {
                'mountpoint': 'exit 0',
                'start-stop-daemon':
                    'case " $* " in *" -t "*) exit 1;; esac\n'
                    'printf "%s\\n" "$@" >> "$CALLS"',
                # Any reintroduction of synchronous readiness polling fails.
                'iwctl': 'exit 99',
                'sleep': 'exit 99',
            }.items():
                path = commands / name
                path.write_text('#!/bin/sh\n' + body + '\n')
                path.chmod(0o755)
            calls = root / 'calls'
            env = dict(os.environ, PATH=f'{commands}:/usr/bin:/bin', CALLS=str(calls))
            subprocess.run(['sh', str(script), 'start'], env=env, check=True, timeout=3)
            args = calls.read_text().splitlines()
            self.assertIn('-b', args)
            self.assertIn('/usr/bin/wifi-setupd', args)
            self.assertIn('--device-key-file', args)
            self.assertNotIn('fixture-password123', calls.read_text())
            subprocess.run(['sh', str(script), 'stop'], env=env, check=True, timeout=3)
            for path, content in before.items():
                self.assertEqual(path.read_bytes(), content)


class WebArchiveTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.archive = self.root / 'portal.tar.gz'
        self.dest = self.root / 'web'

    def archive_with(self, extra=()):
        entries = [('index.html', b'portal'), ('assets/app.wasm', b'\0asm')]
        with tarfile.open(self.archive, 'w:gz') as archive:
            for name, data in [*entries, *extra]:
                if isinstance(data, tarfile.TarInfo):
                    archive.addfile(data)
                else:
                    info = tarfile.TarInfo(name)
                    info.size = len(data)
                    info.mode = 0o777
                    archive.addfile(info, io.BytesIO(data))

    def test_extracts_and_refreshes_only_portal_with_nonexecutable_permissions(self):
        self.archive_with([('assets/app.js', b'javascript')])
        web.extract(self.archive, self.dest)
        self.assertEqual((self.dest / 'assets/app.js').stat().st_mode & 0o777, 0o644)
        self.archive_with()
        web.extract(self.archive, self.dest)
        self.assertFalse((self.dest / 'assets/app.js').exists())

    def test_rejects_traversal_links_duplicates_and_secrets_before_replacing_previous(self):
        self.archive_with()
        web.extract(self.archive, self.dest)
        link = tarfile.TarInfo('assets/link.js')
        link.type, link.linkname = tarfile.SYMTYPE, '/etc/passwd'
        for name, data in [('assets/../../outside.js', b'bad'), ('/index.html', b'bad'),
                           ('device-key', b'secret'), ('index.html', b'duplicate'),
                           ('assets/link.js', link), ('assets/source.rs', b'source')]:
            with self.subTest(name=name):
                self.archive_with([(name, data)])
                with self.assertRaises(ValueError):
                    web.extract(self.archive, self.dest)
                self.assertEqual((self.dest / 'index.html').read_text(), 'portal')


if __name__ == '__main__':
    unittest.main()
