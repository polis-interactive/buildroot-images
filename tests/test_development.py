"""Development image authorization, isolation and state installation boundaries."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

from src.common import Context, UserError
from src.profiles import development, inputs, resolver
from src.profiles.options import Group

REPO = Path(__file__).resolve().parents[1]


class DevelopmentTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        br = self.root / 'buildroot'
        (br / 'configs').mkdir(parents=True)
        (br / 'Makefile').write_text('BR2_VERSION_EPOCH = 1788535000\n')
        shutil.copy(REPO / 'profiles/base/upstream_defconfig', br / 'configs/raspberrypi4_64_defconfig')
        self.ctx = Context(REPO, br, br / 'dl', self.root / '.snapshots')
        self.key = development.ensure_key(self.root)

    def plan(self, name='polis-rpi4-dev', **kwargs):
        with patch.object(resolver, 'input_manifest', return_value=[]):
            return resolver.resolve(self.ctx, name, **kwargs)

    def test_key_generation_is_local_and_reuses_identity(self):
        private = self.key.with_suffix('')
        before = private.read_bytes()
        self.assertEqual(private.stat().st_mode & 0o777, 0o600)
        self.assertEqual(development.ensure_key(self.root), self.key)
        self.assertEqual(private.read_bytes(), before)
        with self.assertRaisesRegex(UserError, 'public-key'):
            development.public_key(private)
        self.key.unlink()
        with self.assertRaisesRegex(UserError, 'exists without its .pub'):
            development.ensure_key(self.root)
        self.assertEqual(private.read_bytes(), before)

    def test_dev_keeps_layout_and_excludes_speed_restriction(self):
        plan = self.plan(ssh_public_key=self.key)
        for symbol in ('BR2_REPRODUCIBLE', 'BR2_TOOLCHAIN_EXTERNAL_BOOTLIN_AARCH64_MUSL_STABLE',
                       'BR2_TARGET_ROOTFS_EROFS', 'BR2_PACKAGE_IWD', 'BR2_PACKAGE_DBUS',
                       'BR2_PACKAGE_READLINE', 'BR2_PACKAGE_DROPBEAR', 'BR2_PACKAGE_AVAHI_DAEMON'):
            self.assertEqual(plan.values['buildroot'][symbol], 'y', symbol)
        self.assertEqual(plan.values['buildroot']['BR2_TARGET_GENERIC_HOSTNAME'], 'polis-dev')
        self.assertEqual(plan.values['kernel']['CONFIG_BCMGENET'], 'y')
        self.assertNotIn('no-ethernet', plan.groups)
        self.assertNotIn('BR2_PACKAGE_DROPBEAR', self.plan('polis-rpi4-base').values['buildroot'])
        self.assertNotIn('BR2_PACKAGE_READLINE', self.plan('polis-rpi4-speed').values['buildroot'])
        profile = resolver.load_profile(REPO / 'profiles/polis-rpi4-dev.toml')
        from dataclasses import replace
        with patch.object(resolver, 'load_profile', return_value=replace(profile, groups=(*profile.groups, Group.NO_ETHERNET))):
            with self.assertRaisesRegex(UserError, 'ethernet is disabled'):
                self.plan(ssh_public_key=self.key)

    def test_public_key_is_a_fingerprinted_input_not_private_material(self):
        plan = self.plan(ssh_public_key=self.key)
        commented = self.root / 'commented.pub'
        commented.write_text(development.public_key(self.key).strip() + ' different comment\n')
        self.assertEqual(plan.fingerprint, self.plan(ssh_public_key=commented).fingerprint)
        other = development.ensure_key(self.root / 'other')
        self.assertNotEqual(plan.fingerprint, self.plan(ssh_public_key=other).fingerprint)
        output = self.root / '.output'
        resolver.write_generated(self.ctx, plan, output)
        self.assertEqual((output / 'profile/authorized_keys').read_text(), development.public_key(self.key))
        self.assertNotIn('PRIVATE KEY', ''.join(p.read_text() for p in (output / 'profile').iterdir()))
        with self.assertRaisesRegex(UserError, 'requires the development-access'):
            self.plan('polis-rpi4-base', ssh_public_key=self.key)

    def test_different_buildroot_inputs_are_rejected(self):
        with self.assertRaisesRegex(UserError, 'Buildroot inputs differ'):
            inputs.verify_inputs(self.ctx, self.plan(ssh_public_key=self.key))

    def test_install_uses_persistent_links_and_only_public_authorization(self):
        spec = importlib.util.spec_from_file_location('install_dev', REPO / 'board/rpi4/install-development.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        target = self.root / 'target'
        (target / 'etc').mkdir(parents=True)
        (target / 'etc/dropbear').symlink_to('/var/run/dropbear')
        (target / 'var/lib').mkdir(parents=True)
        (target / 'var/lib/dbus').symlink_to('/tmp/dbus')
        output = self.root / '.output'
        resolver.write_generated(self.ctx, self.plan(ssh_public_key=self.key), output)
        module.install(target, output / 'profile')
        module.install(target, output / 'profile')
        self.assertEqual((target / 'etc/dropbear').readlink(), Path('/data/config/dropbear'))
        self.assertEqual((target / 'root/.ssh').readlink(), Path('/data/config/ssh'))
        self.assertTrue((target / 'var/lib/dbus').is_dir())
        self.assertFalse((target / 'var/lib/dbus').is_symlink())
        self.assertEqual((target / 'usr/share/polis-development/authorized_keys').read_text(), development.public_key(self.key))

    @unittest.skipUnless(shutil.which('busybox') and shutil.which('cc'), 'needs native BusyBox and C compiler')
    def test_application_start_stop_restart_and_crash_recovery(self):
        data = self.root / 'data'
        (data / 'bin').mkdir(parents=True)
        (data / 'log').mkdir()
        runtime = self.root / 'run'
        runtime.mkdir()
        subprocess.run(['cc', '-x', 'c', '-o', str(data / 'bin/dev-app'), '-'],
                       input='#include <stdio.h>\n#include <unistd.h>\nint main(void) { puts("started"); fflush(stdout); for (;;) pause(); }\n',
                       text=True, check=True)
        commands = self.root / 'commands'
        commands.mkdir()
        (commands / 'start-stop-daemon').symlink_to(shutil.which('busybox'))
        source = REPO / 'profiles/groups/development-access/rootfs-overlay/etc/init.d/S90dev-app'
        script = self.root / 'service'
        script.write_text(source.read_text().replace('/data', str(data)).replace('/run/', str(runtime) + '/'))
        script.chmod(0o755)
        env = dict(os.environ, PATH=str(commands) + ':' + os.environ['PATH'])
        def service(action):
            subprocess.run([script, action], check=True, env=env, capture_output=True)
        self.addCleanup(lambda: service('stop'))
        def started():
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                log = data / 'log/dev-app.log'
                if log.exists() and 'started' in log.read_text():
                    return int((runtime / 'polis-dev-app.pid').read_text())
                time.sleep(0.02)
            self.fail('application did not start or persist its log')
        service('start')
        pid = started()
        self.assertEqual(Path(f'/proc/{pid}/cwd').resolve(), data)
        service('start')
        self.assertEqual(int((runtime / 'polis-dev-app.pid').read_text()), pid)
        os.kill(pid, signal.SIGTERM)
        time.sleep(0.1)
        service('stop')  # a crashed application's stale PID must not block uploads
        service('start')
        self.assertNotEqual(started(), pid)
        self.assertEqual((data / 'log/dev-app.previous.log').read_text(), 'started\n')
        service('restart')
        started()
        service('stop')
        self.assertFalse((runtime / 'polis-dev-app.pid').exists())


if __name__ == '__main__':
    unittest.main()
