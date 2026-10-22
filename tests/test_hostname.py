"""Hostname customization using disposable files, never a device or real mount."""
import hashlib
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from src import cli
from src.artifacts import hostname, images
from src.common import Context, UserError, run
from src.profiles.wifi import write_credentials

REPO = Path(__file__).resolve().parents[1]
HOOK = REPO / 'profiles/base/rootfs-overlay/etc/init.d/polis-hostname'
EROFS = REPO / '.output/host/bin'


class HostnameTests(unittest.TestCase):
    def test_validation_and_cli(self):
        for name in ('pi', 'pi-2', 'a', 'a' * 63):
            self.assertEqual(hostname.validate(name), name)
        for name in ('', '-pi', 'pi-', 'PI', 'pi.local', 'pi_2', 'a' * 64, 'pi\n', 'a b', 'é'):
            with self.subTest(name=name), self.assertRaises(UserError):
                hostname.validate(name)
        self.assertIsNone(cli.parser().parse_args(['flash']).hostname)
        with patch.object(images, 'flash') as flash:
            self.assertEqual(cli.main(['flash', '--hostname', 'pi-2', '--expand-data']), 0)
        self.assertEqual(flash.call_args.kwargs, dict(hostname='pi-2', ap_name=None, expand_data=True))

    def test_ap_name_validation_and_cli(self):
        for name in ('Polis-Setup-2', 'pi_2', 'A' * 32):
            self.assertEqual(hostname.validate_ap_name(name), name)
        for name in ('', 'A' * 33, '../other', 'a/b', 'a b', 'é', 'a\n', '-x', 'x"'):
            with self.subTest(name=name), self.assertRaises(UserError):
                images.flash(None, None, None, ap_name=name)
        with patch.object(images, 'flash') as flash:
            self.assertEqual(cli.main(['flash', '--hostname', 'polis-dev-2', '--ap-name', 'Polis-Setup-2']), 0)
        self.assertEqual(flash.call_args.kwargs,
                         dict(hostname='polis-dev-2', ap_name='Polis-Setup-2', expand_data=False))

    def test_invalid_name_does_not_select_or_write_a_card(self):
        with patch.object(images, '_flash') as write, self.assertRaises(UserError):
            images.flash(None, None, None, hostname='bad.local')
        write.assert_not_called()

    def test_startup_updates_identity_and_preserves_unrelated_hosts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for directory in ('data/config', 'run', 'etc/network'):
                (root / directory).mkdir(parents=True)
            (root / 'etc/hostname').write_text('polis-dev\n')
            (root / 'etc/hosts').write_text('127.0.0.1 localhost\n127.0.1.1 polis-dev polis-dev.local\n'
                                           '192.0.2.1 other # polis-dev comment\n')
            (root / 'etc/network/ethernet').write_text('iface eth0 inet dhcp\n    hostname polis-dev\n')
            # Substitute filesystem roots; simulate bind mounts with copies and
            # hostname(1) with a file, without changing the host's namespaces.
            script = HOOK.read_text()
            for prefix in ('/data/', '/run/', '/etc/'):
                script = script.replace(prefix, str(root) + prefix)
            functions = 'mount() { cp "$3" "$4"; }\nhostname() { cat "$2" >"' + str(root / 'active') + '"; }\n'
            script = script.replace('set -eu\n', 'set -eu\n' + functions)
            def boot():
                return subprocess.run(['sh', '-c', script], capture_output=True, text=True)
            self.assertEqual(boot().returncode, 0)
            self.assertFalse((root / 'active').exists())
            (root / 'data/config/hostname').write_text('polis-2\n')
            result = boot()
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((root / 'active').read_text(), 'polis-2\n')
            self.assertEqual((root / 'etc/hostname').read_text(), 'polis-2\n')
            self.assertIn('polis-2 polis-2.local', (root / 'etc/hosts').read_text())
            self.assertIn('other # polis-dev comment', (root / 'etc/hosts').read_text())
            self.assertIn('hostname polis-2', (root / 'etc/network/ethernet').read_text())
            for bad in ('bad.local', '-bad', 'a' * 64, 'two\nnames', ''):
                (root / 'data/config/hostname').write_text(bad)
                self.assertNotEqual(boot().returncode, 0)
                self.assertEqual((root / 'active').read_text(), 'polis-2\n')


@unittest.skipUnless(all(shutil.which(tool) for tool in ('debugfs', 'e2fsck', 'mkfs.ext4'))
                     and (EROFS / 'mkfs.erofs').exists() and (EROFS / 'dump.erofs').exists(),
                     'requires host e2fsprogs and .output/host/bin erofs-utils')
class HostnameFilesystemTests(unittest.TestCase):
    def test_prepare_preserves_source_and_data_and_rejects_old_images(self):
        with tempfile.TemporaryDirectory(prefix='polis-hostname-test-') as tmp:
            root = Path(tmp)
            seed = root / 'seed'
            (seed / 'config').mkdir(parents=True)
            (seed / 'config/hostname').write_text('previous\n')
            (seed / 'keep').write_text('existing Wi-Fi and application state\n')
            write_credentials(seed, 'fixture-password')
            data = root / 'data.ext4'
            with data.open('wb') as stream:
                stream.truncate(16 << 20)
            run(['mkfs.ext4', '-q', '-F', '-L', 'polis-data', '-d', seed, data], capture=True)
            target = root / 'target/etc/init.d'
            target.mkdir(parents=True)
            shutil.copy(HOOK, target / 'polis-hostname')
            shutil.copy(HOOK.parent / 'mount-data', target / 'mount-data')
            erofs = root / 'root.erofs'
            run([EROFS / 'mkfs.erofs', erofs, root / 'target'], capture=True)
            source = root / 'source.img'
            with source.open('wb') as stream:
                stream.write(bytes(512))
                stream.write(erofs.read_bytes())
                stream.seek(1 << 20)
                stream.write(data.read_bytes())
            before = hashlib.sha256(source.read_bytes()).digest()
            layout = dict(partitions=[{}, dict(start=1), dict(start=2048, size=data.stat().st_size // 512)])
            ctx = Context(REPO, root, root, root)
            work = root / 'work with spaces'
            work.mkdir()
            prepared = hostname.prepare(ctx, source, layout, 'polis-2', work, ap_name='Polis-Setup-2')
            self.assertEqual(run(['debugfs', '-R', 'cat /config/hostname', prepared], capture=True).stdout, 'polis-2\n')
            self.assertEqual(run(['debugfs', '-R', 'cat /wifi-setupd/ssid', prepared], capture=True).stdout, 'Polis-Setup-2\n')
            self.assertEqual(run(['debugfs', '-R', 'cat /keep', prepared], capture=True).stdout,
                             'existing Wi-Fi and application state\n')
            self.assertEqual(prepared.stat().st_size, data.stat().st_size)
            self.assertEqual(hashlib.sha256(source.read_bytes()).digest(), before)
            # Base images can have a completely empty data filesystem.
            run(['debugfs', '-w', '-R', 'rm /config/hostname', data], capture=True)
            run(['debugfs', '-w', '-R', 'rmdir /config', data], capture=True)
            with source.open('r+b') as stream:
                stream.seek(1 << 20)
                stream.write(data.read_bytes())
            prepared = hostname.prepare(ctx, source, layout, 'polis-3', work)
            self.assertEqual(run(['debugfs', '-R', 'cat /config/hostname', prepared], capture=True).stdout, 'polis-3\n')
            # An existing snapshot without the hook must fail during preflight.
            (target / 'mount-data').write_text('#!/bin/sh\nmount /data\n')
            run([EROFS / 'mkfs.erofs', erofs, root / 'target'], capture=True)
            with source.open('r+b') as stream:
                stream.seek(512)
                stream.write(erofs.read_bytes())
            with self.assertRaisesRegex(UserError, 'Regenerate the image'):
                hostname.prepare(ctx, source, layout, 'polis-3', work)


@unittest.skipUnless(all(shutil.which(tool) for tool in ('debugfs', 'e2fsck', 'mkfs.ext4')),
                     'requires host e2fsprogs')
class APFilesystemTests(unittest.TestCase):
    def setUp(self):
        self.tmp = self.enterContext(tempfile.TemporaryDirectory(prefix='polis-ap-test-'))
        self.root = Path(self.tmp)
        self.seed = self.root / 'seed'
        write_credentials(self.seed, r'fixture-password\escaped')
        (self.seed / 'var-lib/iwd/home.psk').write_text('unrelated router credentials\n')
        self.image = self.root / 'source.ext4'
        self.work = self.root / 'work'
        self.work.mkdir()
        self.ctx = Context(self.root, self.root, self.root, self.root)
        self.layout = dict(partitions=[{}, {}, dict(start=0, size=(16 << 20) // 512)])

    def prepare(self, name='Polis-Setup-2'):
        with self.image.open('wb') as stream:
            stream.truncate(16 << 20)
        run(['mkfs.ext4', '-q', '-F', '-L', 'polis-data', '-d', self.seed, self.image], capture=True)
        before = hashlib.sha256(self.image.read_bytes()).digest()
        with patch.object(hostname, 'check_hostname_support', side_effect=AssertionError('AP needs no root hook')):
            result = hostname.prepare(self.ctx, self.image, self.layout, None, self.work, ap_name=name)
        self.assertEqual(hashlib.sha256(self.image.read_bytes()).digest(), before)
        return result

    def test_rename_preserves_password_settings_and_router_profiles(self):
        result = self.prepare()
        def cat(path):
            return run(['debugfs', '-R', f'cat {path}', result], capture=True).stdout
        self.assertEqual(cat('/wifi-setupd/ssid'), 'Polis-Setup-2\n')
        for destination, source in (
                ('/wifi-setupd/device-key', 'wifi-setupd/device-key'),
                ('/var-lib/iwd/ap/Polis-Setup-2.ap', 'var-lib/iwd/ap/Polis-Setup.ap'),
                ('/var-lib/iwd/home.psk', 'var-lib/iwd/home.psk')):
            self.assertEqual(cat(destination), (self.seed / source).read_text())
        self.assertFalse(run(['debugfs', '-R', 'stat /var-lib/iwd/ap/Polis-Setup.ap', result], capture=True).stdout)
        for path in ('/wifi-setupd/ssid', '/var-lib/iwd/ap/Polis-Setup-2.ap'):
            info = run(['debugfs', '-R', f'stat {path}', result], capture=True).stdout
            self.assertRegex(info, r'Mode:\s+0600')
            self.assertRegex(info, r'User:\s+0\s+Group:\s+0')

    def test_same_name_is_noop(self):
        result = self.prepare('Polis-Setup')
        self.assertEqual(result.read_bytes(), self.image.read_bytes())

    def test_missing_wifi_configuration_is_rejected(self):
        (self.seed / 'wifi-setupd/ssid').unlink()
        with self.assertRaisesRegex(UserError, 'initialized Wi-Fi image'):
            self.prepare()

    def test_existing_profile_collision_is_rejected(self):
        (self.seed / 'var-lib/iwd/ap/Polis-Setup-2.ap').write_text('preserve another AP\n')
        with self.assertRaisesRegex(UserError, 'already exists'):
            self.prepare()

    def test_unsafe_existing_ssid_is_rejected(self):
        (self.seed / 'wifi-setupd/ssid').write_text('../not-a-profile\n')
        with self.assertRaises(UserError):
            self.prepare()


if __name__ == '__main__':
    unittest.main()
