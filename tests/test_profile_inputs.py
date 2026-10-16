"""Shared lock enforcement and content-based profile/cache boundaries."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

from src.build.incremental import Tracker
from src.common import Context, UserError
from src.profiles import inputs, resolver, workflow
from src.profiles.image import assemble
from profile_fixtures import PREMERGE_GROUPS, prepare_profiles, write_profile

REPO = Path(__file__).resolve().parents[1]


class SharedInputTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        repo = Path(self.temp.name) / 'repo'
        repo.mkdir()
        prepare_profiles(repo)
        write_profile(repo, 'fixture-layout', (*PREMERGE_GROUPS,
                      resolver.Group.HEADLESS, resolver.Group.CUTDOWN_FIRMWARE,
                      resolver.Group.LZ4_KERNEL, resolver.Group.BOOT_RAMDISK))
        # Only tiny fixtures are needed for input hashing; nothing is built.
        for name in ('src', 'board', 'build-support', 'package', 'patches'):
            (repo / name).mkdir()
        for name in ('Config.in', 'external.mk', 'external.desc', 'build.toml'):
            shutil.copy(REPO / name, repo / name)
        for group in resolver.Group:
            data = resolver.load_group(repo, group)
            for name in data.get('inputs', []) + data.get('post_build', []):
                path = repo / name
                if not path.exists():
                    path.parent.mkdir(parents=True, exist_ok=True)
                    if (REPO / name).is_dir():
                        path.mkdir()
                    else:
                        path.write_text('fixture\n')
        # A parameterized Buildroot checkout may live inside the external repo.
        br = repo / '.local/buildroot'
        for name in ('arch', 'toolchain', 'linux', 'package', 'system', 'fs',
                     'support/scripts', 'support/kconfig', 'configs',
                     'board/raspberrypi', 'board/raspberrypi4-64'):
            (br / name).mkdir(parents=True, exist_ok=True)
        for name in ('Config.in', 'Config.in.legacy'):
            (br / name).write_text('fixture\n')
        (br / 'Makefile').write_text('BR2_VERSION_EPOCH = 1788535000\n')
        shutil.copy(repo / 'profiles/base/upstream_defconfig', br / 'configs/raspberrypi4_64_defconfig')
        self.ctx = Context(repo, br, br / 'dl', repo / '.snapshots')
        self.lock = repo / inputs.LOCK_PATH
        pins = json.loads(self.lock.read_text())
        manifest = resolver.input_manifest(self.ctx, repo / 'profiles/fixture-empty.toml')
        pins['buildroot_inputs_sha256'] = resolver.digest([e for e in manifest if e['root'] == 'buildroot'])
        self.lock.write_text(json.dumps(pins) + '\n')

    def plans(self):
        return {name: resolver.resolve(self.ctx, name) for name in self.ctx.profiles}

    def test_selections_share_minimal_lock_and_buildroot_epoch(self):
        pins = json.loads(self.lock.read_text())
        self.assertEqual(set(pins), {'schema', 'buildroot_url', 'buildroot_archive_sha256', 'buildroot_inputs_sha256'})
        for name, plan in self.plans().items():
            with self.subTest(profile=name):
                self.assertEqual(inputs.verify_inputs(self.ctx, plan), pins)
                self.assertEqual(plan.source_date_epoch, 1788535000)
                self.assertEqual(plan.values['buildroot']['BR2_REPRODUCIBLE'], 'y')
                self.assertIn('Makefile', [e['path'] for e in plan.inputs if e['root'] == 'buildroot'])

    def test_touches_preserve_fingerprints_output_paths_and_lock(self):
        before = self.plans()
        for root in (self.ctx.repo / 'profiles', self.ctx.buildroot):
            for path in root.rglob('*'):
                if path.is_file() and not path.is_symlink():
                    os.utime(path, (1234567890, 1234567890))
        for name, plan in self.plans().items():
            self.assertEqual(before[name].fingerprint, plan.fingerprint)
            self.assertEqual(workflow.output_for(self.ctx, before[name]), workflow.output_for(self.ctx, plan))
            inputs.verify_inputs(self.ctx, plan)

    def test_unselected_profile_and_overlay_changes_preserve_fingerprints(self):
        before = self.plans()
        dev = self.ctx.repo / 'profiles/fixture-development.toml'
        dev.write_text(dev.read_text() + '\n# development-only edit\n')
        overlay = self.ctx.repo / 'profiles/groups/development-access/rootfs-overlay/etc/default/dropbear'
        overlay.write_text(overlay.read_text() + '\n# development-only edit\n')
        after = self.plans()
        for name in ('fixture-empty', 'fixture-eeprom'):
            self.assertEqual(before[name].fingerprint, after[name].fingerprint)
        self.assertNotEqual(before['fixture-development'].fingerprint, after['fixture-development'].fingerprint)

    def test_real_content_and_permission_changes_are_inputs(self):
        before = self.plans()
        path = self.ctx.repo / 'profiles/base/rootfs-overlay/etc/init.d/mount-data'
        path.write_text(path.read_text() + '\n# relevant content\n')
        os.utime(path, (1, 1))
        changed = self.plans()
        path.chmod(path.stat().st_mode ^ 0o100)
        for name, plan in self.plans().items():
            self.assertNotEqual(before[name].fingerprint, changed[name].fingerprint)
            self.assertNotEqual(changed[name].fingerprint, plan.fingerprint)

    def test_changed_buildroot_is_rejected_for_each_selection(self):
        path = self.ctx.buildroot / 'package/new-package.mk'
        path.write_text('NEW_VERSION = 1\n')
        for plan in self.plans().values():
            with self.assertRaisesRegex(UserError, 'Buildroot inputs differ'):
                inputs.verify_inputs(self.ctx, plan)

    def test_generated_files_are_not_rewritten_when_content_is_unchanged(self):
        for name, plan in self.plans().items():
            output = self.ctx.repo / '.output' / name
            resolver.write_generated(self.ctx, plan, output)
            paths = list((output / 'profile').iterdir())
            for path in paths:
                os.utime(path, (1234567890, 1234567890))
            before = {path: path.stat().st_mtime_ns for path in paths}
            resolver.write_generated(self.ctx, plan, output)
            self.assertEqual(before, {path: path.stat().st_mtime_ns for path in paths})

    def test_component_rules_ignore_unrelated_profile_edits(self):
        # Use real repository rules; simulate recorded compilation outputs.
        ctx = Context(REPO, self.ctx.buildroot, self.ctx.downloads, self.ctx.snapshots)
        output = self.ctx.repo / 'component-output'
        output.mkdir()
        tracker = Tracker(ctx, 'fixture-empty', output)
        # Point the same policy at our editable fixture for the fingerprint check.
        tracker.ctx = self.ctx
        tracker.bindings['repo'] = str(self.ctx.repo)
        rule = next(rule for rule in tracker.steps if rule['name'] == 'kernel-source')
        before = tracker.fingerprint(rule, {})
        path = self.ctx.repo / 'profiles/fixture-development.toml'
        path.write_text(path.read_text() + '\n# unrelated edit\n')
        self.assertEqual(before, tracker.fingerprint(rule, {}))

    @unittest.skipUnless(os.environ.get('POLIS_TEST_HOST_DIR'), 'set POLIS_TEST_HOST_DIR to built Buildroot host tools')
    def test_assembly_is_reproducible_with_real_filesystem_tools(self):
        host = Path(os.environ['POLIS_TEST_HOST_DIR']).resolve()
        real_run = subprocess.run
        def run(args, **kwargs):
            # Use opaque payloads for the two compiler/DTB helpers. The real
            # filesystem tools still assemble both FAT layers and ext4/MBR.
            if str(args[1]).endswith('lz4-loader/build.py'):
                Path(args[3]).write_bytes(b'fixture compressed kernel')
            elif str(args[1]).endswith('premerge-dtb.py'):
                Path(args[3]).write_bytes(b'fixture premerged device tree')
            else:
                return real_run(args, **kwargs)
        for name, plan in self.plans().items():
            with self.subTest(profile=name):
                output = self.ctx.repo / 'assembly' / name
                images = output / 'images'
                images.mkdir(parents=True)
                (output / 'build').mkdir()
                names = ('Image', 'bcm2711-rpi-4-b.dtb', 'rootfs.erofs',
                         'rpi-firmware/start4.elf', 'rpi-firmware/fixup4.dat',
                         'rpi-firmware/start4cd.elf', 'rpi-firmware/fixup4cd.dat',
                         'rpi-firmware/config.txt', 'rpi-firmware/cmdline.txt',
                         'rpi-firmware/overlays/test.dtbo')
                for filename in names:
                    path = images / filename
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(filename.encode())
                if plan.enabled(resolver.Group.WIFI_SETUPD):
                    plan.wifi_password = 'fixture-password123'
                    (output / 'target/var/lib/dbus').mkdir(parents=True)
                resolver.write_generated(self.ctx, plan, output)
                env = dict(os.environ, BASE_DIR=str(output), BR2_EXTERNAL_POLIS_PATH=str(REPO),
                           BINARIES_DIR=str(images), HOST_DIR=str(host), BUILD_DIR=str(output / 'build'))
                results = []
                for _ in range(2):
                    for filename in names:
                        os.utime(images / filename, None)
                    with patch('src.profiles.image.subprocess.run', side_effect=run):
                        assemble(env)
                    results.append({filename: hashlib.sha256((images / filename).read_bytes()).hexdigest()
                                    for filename in ('boot.vfat', 'data.ext4', 'sdcard.img')})
                    time.sleep(2)
                self.assertEqual(results[0], results[1])


if __name__ == '__main__':
    unittest.main()
