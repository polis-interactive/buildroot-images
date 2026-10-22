"""Composition, image packaging, and reviewed-build boundaries without hardware."""
import contextlib
from dataclasses import replace
import importlib.util
import io
import itertools
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from src.common import Context, UserError
from src.profiles import inputs, resolver, workflow, provision
from src.profiles.image import assemble, recipe
from src.profiles.options import BASE_GROUPS, Capability, Group
from profile_fixtures import PREMERGE_GROUPS, WIFI_GROUPS, prepare_repo, write_profile

REPO = Path(__file__).resolve().parents[1]


class ProfileBuildTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        br = self.root / 'buildroot'
        (br / 'configs').mkdir(parents=True)
        (br / 'Makefile').write_text('BR2_VERSION_EPOCH = 1788535000\n')
        shutil.copy2(REPO / 'profiles/base/upstream_defconfig', br / 'configs/raspberrypi4_64_defconfig')
        repo = self.root / 'repo'
        prepare_repo(repo)
        self.ctx = Context(repo, br, br / 'dl', self.root / '.snapshots')
        self.output = self.root / '.output'
        manifest = patch.object(resolver, 'input_manifest', return_value=[
            dict(root='repo', path='profiles/fixture-eeprom.toml', sha256='a' * 64)])
        manifest.start()
        self.addCleanup(manifest.stop)
        self.enterContext(patch.object(inputs, 'verify_inputs', return_value=
                                      json.loads((REPO / inputs.LOCK_PATH).read_text())))

    def plan(self, name='fixture-eeprom', groups=None, requires=None):
        if groups is not None or requires is not None:
            name = 'fixture-selection'
            write_profile(self.ctx.repo, name, groups or (), requires or ())
        return resolver.resolve(self.ctx, name)

    def test_every_enum_has_a_valid_manifest(self):
        for group in Group:
            self.assertEqual(resolver.load_group(REPO, group)['id'], group.value)

    def test_shared_core_generates_static_erofs_configuration(self):
        plan = self.plan('fixture-empty')
        self.assertEqual(set(plan.groups), {g.value for g in BASE_GROUPS})
        br = resolver.write_generated(self.ctx, plan, self.output)
        for symbol in ('BR2_TOOLCHAIN_EXTERNAL_BOOTLIN_AARCH64_MUSL_STABLE',
                       'BR2_TARGET_ROOTFS_EROFS', 'BR2_POLIS_PREFER_STATIC'):
            self.assertEqual(br[symbol], 'y')
        self.assertEqual(plan.values['busybox']['CONFIG_STATIC'], 'y')

    def test_wifi_password_is_a_reviewed_input_without_plaintext_in_audits(self):
        with patch.dict(os.environ, POLIS_WIFI_SETUP_PASSWORD='image-password123'):
            first = self.plan(groups=WIFI_GROUPS)
            without_wifi = self.plan('fixture-empty')
        with patch.dict(os.environ, POLIS_WIFI_SETUP_PASSWORD='different-password456'):
            second = self.plan(groups=WIFI_GROUPS)
            self.assertEqual(without_wifi.fingerprint, self.plan('fixture-empty').fingerprint)
        self.assertNotEqual(first.fingerprint, second.fingerprint)
        self.assertNotIn(first.wifi_password, repr(first))
        self.assertNotIn(first.wifi_password, json.dumps(workflow.new_audit(first)))
        resolver.write_generated(self.ctx, first, self.output)
        path = self.output / 'profile/wifi-password'
        self.assertEqual(path.read_text(), first.wifi_password)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        before = path.stat().st_mtime_ns
        resolver.write_generated(self.ctx, first, self.output)
        self.assertEqual(path.stat().st_mtime_ns, before)

    def test_missing_wifi_password_fails_before_buildroot_configuration(self):
        plan = replace(self.plan(groups=WIFI_GROUPS), wifi_password='')
        with patch.object(workflow, 'run') as run:
            with self.assertRaisesRegex(UserError, 'POLIS_WIFI_SETUP_PASSWORD'):
                workflow.configure(self.ctx, plan, self.output, False)
            run.assert_not_called()

    def test_no_wifi_group_excludes_services_firmware_and_kernel_drivers(self):
        without_wifi = self.plan(groups=[Group.NO_WIFI])
        for symbol in ('BR2_PACKAGE_IWD', 'BR2_PACKAGE_POLIS_WIFI_SETUPD',
                       'BR2_PACKAGE_BRCMFMAC_SDIO_FIRMWARE_RPI_WIFI',
                       'BR2_PACKAGE_AVAHI', 'BR2_PACKAGE_DBUS', 'BR2_PACKAGE_OPENRESOLV'):
            self.assertEqual(without_wifi.values['buildroot'][symbol], 'n')
        for symbol in ('CONFIG_WLAN', 'CONFIG_CFG80211', 'CONFIG_MAC80211', 'CONFIG_BRCMFMAC'):
            self.assertEqual(without_wifi.values['kernel'][symbol], 'n')
        self.assertNotIn('etc/modules-load.d/wifi.conf', without_wifi.values['rootfs'])
        self.assertNotIn('board/rpi4/install-wifi.py', without_wifi.post_build)
        with self.assertRaisesRegex(UserError, 'wifi.*disabled'):
            self.plan(groups=[Group.NO_WIFI], requires=[Capability.WIFI])

    def test_group_order_cannot_change_generated_output_or_fingerprint(self):
        p = self.plan(groups=WIFI_GROUPS)
        reverse = self.plan(groups=reversed(p.profile.groups))
        self.assertEqual(p.fingerprint, reverse.fingerprint)
        self.assertEqual(p.values, reverse.values)
        self.assertEqual(p.overlays, reverse.overlays)

    def test_development_mode_reuses_output_and_disables_provisioning(self):
        final = self.plan()
        dev = resolver.resolve(self.ctx, 'fixture-eeprom', development=True)
        self.assertNotEqual(final.fingerprint, dev.fingerprint)
        self.assertEqual(workflow.output_for(self.ctx, final), workflow.output_for(self.ctx, dev))
        self.assertEqual(final.values['buildroot']['BR2_PACKAGE_POLIS_FIRSTBOOT'], 'y')
        self.assertNotIn('BR2_PACKAGE_POLIS_FIRSTBOOT', dev.values['buildroot'])
        resolver.write_generated(self.ctx, dev, self.output)
        image = json.loads((self.output / 'profile/image-plan.json').read_text())
        self.assertTrue(image['development'])
        self.assertIn('bootloader_update=0', (self.output / 'profile/config.txt').read_text())
        self.configure()
        with patch.object(workflow, 'run', side_effect=self.fake_configure):
            workflow.configure(self.ctx, dev, self.output, False)
        workflow.assert_current(self.ctx, dev, self.output)
        self.assertTrue((self.output / 'profile/reinstall-target').exists())

    def test_image_metadata_never_requests_boot_time_storage_expansion(self):
        images = self.output / 'images'
        images.mkdir(parents=True)
        for development in (False, True):
            with self.subTest(development=development):
                plan = resolver.resolve(self.ctx, 'fixture-empty', development=development)
                resolver.write_generated(self.ctx, plan, self.output)
                # Exercise metadata generation without building an image.
                with patch('src.profiles.image.subprocess.run'):
                    assemble(dict(BASE_DIR=str(self.output), BR2_EXTERNAL_POLIS_PATH=str(REPO),
                                  BINARIES_DIR=str(images), HOST_DIR=str(self.output / 'host'),
                                  BUILD_DIR=str(self.output / 'build')))
                metadata = json.loads((images / 'provisioning.json').read_text())
                self.assertIs(metadata['expand_data'], False)
                self.assertEqual(metadata['enabled'], not development)

    def test_missing_prerequisite_rejected(self):
        with self.assertRaisesRegex(UserError, 'requires explicitly selected group headless'):
            self.plan(groups=[Group.CUTDOWN_FIRMWARE], requires=[])

    def test_conflicting_settings_rejected(self):
        original = resolver.load_group
        def conflicting(repo, group):
            result = original(repo, group)
            if group is Group.KERNEL_SIZE:
                result['kernel']['CONFIG_DRM'] = 'y'
            return result
        with patch.object(resolver, 'load_group', side_effect=conflicting):
            with self.assertRaisesRegex(UserError, 'conflicting assignments'):
                self.plan(groups=[Group.HEADLESS, Group.KERNEL_SIZE])

    def test_cutdown_selection_controls_generated_firmware(self):
        for enabled in (True, False):
            with self.subTest(cutdown=enabled):
                groups = [Group.HEADLESS]
                if enabled:
                    groups.append(Group.CUTDOWN_FIRMWARE)
                resolver.write_generated(self.ctx, self.plan(groups=groups), self.output)
                firmware = (self.output / 'profile/config.txt').read_text()
                self.assertEqual('start_file=start4.elf' in firmware, not enabled)
                self.assertEqual('gpu_mem=16' in firmware, enabled)

    def test_premerge_selection_controls_overlay_directives(self):
        for enabled in (True, False):
            with self.subTest(premerge=enabled):
                groups = [g for g in PREMERGE_GROUPS
                          if enabled or g is not Group.PREMERGE_OVERLAYS]
                plan = self.plan(groups=groups)
                resolver.write_generated(self.ctx, plan, self.output)
                firmware = (self.output / 'profile/config.txt').read_text()
                self.assertEqual('dtoverlay=disable-bt' in firmware, not enabled)
                self.assertEqual('dtoverlay=dwc2,dr_mode=peripheral' in firmware, not enabled)
                self.assertEqual('BR2_PACKAGE_HOST_POLIS_DTMERGE' in plan.values['buildroot'], enabled)

    def test_premerge_without_collector_does_not_select_target_diagnostic_tools(self):
        p = self.plan(groups=PREMERGE_GROUPS)
        br = resolver.write_generated(self.ctx, p, self.output)
        self.assertEqual(br['BR2_PACKAGE_HOST_POLIS_DTMERGE'], 'y')
        self.assertNotIn('BR2_PACKAGE_BOOT_DIAGNOSTICS_TOOLS', br)
        self.assertNotIn('BR2_PACKAGE_POLIS_BOOT_DIAGNOSTICS', br)

    def test_collector_does_not_require_gpio_uart_or_usb(self):
        p = self.plan(groups=[Group.POLIS_RUNTIME, Group.BOOT_DIAGNOSTICS],
                      requires=[Capability.BOOT_DIAGNOSTICS])
        self.assertEqual(p.values['buildroot']['BR2_PACKAGE_POLIS_BOOT_DIAGNOSTICS'], 'y')
        self.assertNotIn('CONFIG_RASPBERRYPI_GPIOMEM', p.values['kernel'])
        self.assertNotIn('CONFIG_GPIO_CDEV', p.values['kernel'])
        self.assertNotIn('usb-diagnostics', p.groups)
        self.assertNotIn('uart-diagnostics', p.groups)

    def fake_configure(self, args):
        shutil.copy2(self.output / 'profile/defconfig', self.output / '.config')

    def configure(self, display=False):
        p = self.plan()
        with patch.object(workflow, 'run', side_effect=self.fake_configure), contextlib.redirect_stdout(io.StringIO()) as log:
            workflow.configure(self.ctx, p, self.output, display)
        return p, log.getvalue()

    def test_audit_is_always_saved_but_only_staged_configuration_displays_it(self):
        p, quiet = self.configure()
        self.assertIn('Configuration audit:', quiet)
        self.assertNotIn('Differences from', quiet)
        self.assertTrue((self.output / 'profile/audit.json').is_file())
        _, staged = self.configure(display=True)
        self.assertIn('Differences from', staged)
        self.assertIn('NET_INSTALL_ENABLED', staged)
        workflow.assert_current(self.ctx, p, self.output)

    def test_unchanged_configuration_reuses_files_and_completed_audit(self):
        plan, _ = self.configure()
        audit = json.loads((self.output / 'profile/audit.json').read_text())
        audit['state'] = 'built'
        workflow.save_audit(self.output, audit)
        paths = [self.output / '.config', *(self.output / 'profile').iterdir()]
        before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in paths}
        with patch.object(workflow, 'run') as run, contextlib.redirect_stdout(io.StringIO()):
            workflow.configure(self.ctx, plan, self.output, False)
            run.assert_not_called()
        self.assertEqual(before, {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in paths})

    def test_configuration_checks_the_shared_lock_for_each_selection(self):
        for name in self.ctx.profiles:
            with self.subTest(profile=name), patch.object(inputs, 'verify_inputs',
                    side_effect=UserError('Buildroot inputs differ')), patch.object(workflow, 'run') as run:
                with self.assertRaisesRegex(UserError, 'Buildroot inputs differ'):
                    workflow.configure(self.ctx, self.plan(name), self.output, False)
                run.assert_not_called()

    def test_staged_build_rejects_changed_inputs_or_generated_files(self):
        p, _ = self.configure()
        with self.assertRaisesRegex(UserError, 'inputs changed'):
            workflow.assert_current(self.ctx, replace(p, fingerprint='b' * 64), self.output)
        (self.output / 'profile/kernel.fragment').write_text('CONFIG_DRM=y\n')
        with self.assertRaisesRegex(UserError, 'configuration changed'):
            workflow.assert_current(self.ctx, p, self.output)

    def test_failed_configuration_can_be_retried_but_cannot_be_built(self):
        p = self.plan()
        def fail(args):
            self.fake_configure(args)
            raise UserError('simulated interruption')
        with patch.object(workflow, 'run', side_effect=fail):
            with self.assertRaises(UserError):
                workflow.configure(self.ctx, p, self.output, False)
        with self.assertRaisesRegex(UserError, 'configure this profile first'):
            workflow.assert_current(self.ctx, p, self.output)
        self.configure()
        workflow.assert_current(self.ctx, p, self.output)

    def test_resolved_kconfig_cannot_silently_drop_requested_settings(self):
        with self.assertRaisesRegex(UserError, 'requested.*effective'):
            workflow.check_values({'CONFIG_STATIC': 'y'}, {}, 'BusyBox', REPO, self.output)

    def test_image_with_capture_prepares_eeprom_and_completes_snapshot(self):
        with patch.object(workflow, 'configure') as configure, \
                patch.object(workflow, 'build') as build, \
                patch.object(workflow, 'assert_current'), \
                patch.object(provision, 'capture_for', return_value=self.root / 'capture'), \
                patch.object(workflow, 'prepare_eeprom') as prepare, \
                patch.object(workflow.snapshots, 'snapshot') as snapshot:
            workflow.execute(self.ctx, 'image', 'fixture-eeprom', self.output, 'speed',
                             capture=self.root / 'capture')
            self.assertFalse(configure.call_args.kwargs['display'])
            build.assert_called_once()
            prepare.assert_called_once_with(self.ctx, unittest.mock.ANY, self.output, self.root / 'capture')
            snapshot.assert_called_once_with(self.ctx, self.output, 'speed')

    def test_snapshot_rejects_failed_build_and_changed_artifacts(self):
        p, _ = self.configure()
        with self.assertRaisesRegex(UserError, 'no completed'):
            workflow.assert_current(self.ctx, p, self.output, built=True)
        images = self.output / 'images'
        images.mkdir()
        image = images / 'sdcard.img'
        image.write_bytes(b'original image')
        audit = workflow.new_audit(p)
        audit.update(state='built', artifacts=[dict(root='output', path='images/sdcard.img',
                                                   sha256=resolver.file_hash(image))])
        workflow.save_audit(self.output, audit)
        workflow.assert_current(self.ctx, p, self.output, built=True)
        image.write_bytes(b'changed')
        with self.assertRaisesRegex(UserError, 'artifact changed'):
            workflow.assert_current(self.ctx, p, self.output, built=True)

    def test_eeprom_preparation_is_profile_driven(self):
        with patch.object(provision, 'run') as run, contextlib.redirect_stdout(io.StringIO()) as output:
            provision.prepare(self.ctx, 'fixture-empty')
            self.assertIn('nothing to do', output.getvalue())
            run.assert_not_called()

    def test_static_policy_only_requires_selected_packages(self):
        spec = importlib.util.spec_from_file_location('static_policy', REPO / 'board/rpi4/check-static.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertEqual(module.selected_programs(''), ['bin/busybox'])
        self.assertEqual(module.selected_programs('BR2_PACKAGE_IWD=y\n'), ['bin/busybox', 'usr/libexec/iwd'])
        self.assertEqual(module.selected_programs('BR2_PACKAGE_POLIS_BOOT_DIAGNOSTICS=y\n'),
                         ['bin/busybox', 'usr/bin/polis-boot-diagnostics', 'usr/bin/vclog', 'usr/bin/vcgencmd'])

    def test_image_layout_combinations_and_compact_capacity(self):
        images = self.root / 'images'
        # Measured payload sizes, using sparse fixtures.
        sizes = {'Image': 10621440, 'Image.lz4boot': 5146688,
                 'bcm2711-rpi-4-b.dtb': 56369, 'bcm2711-rpi-4-b.premerged.dtb': 56369,
                 'rpi-firmware/start4cd.elf': 849948, 'rpi-firmware/fixup4cd.dat': 3272,
                 'rpi-firmware/start4.elf': 2300000, 'rpi-firmware/fixup4.dat': 5500,
                 'rpi-firmware/config.txt': 505, 'rpi-firmware/cmdline.txt': 125,
                 'rpi-firmware/overlays/disable-bt.dtbo': 1200}
        for name, size in sizes.items():
            path = images / name
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open('wb') as stream:
                stream.truncate(size)
        for flags in itertools.product((False, True), repeat=4):
            plan = dict(zip(('lz4', 'premerge', 'cutdown', 'ramdisk'), flags))
            text, size = recipe(images, plan)
            self.assertEqual('image boot.img {' in text, plan['ramdisk'])
            self.assertEqual('file overlays' in text, not plan['premerge'])
            self.assertEqual('start4cd.elf' in text, plan['cutdown'])
            self.assertEqual('Image.lz4boot' in text, plan['lz4'])
            if all(flags):
                self.assertEqual(size, 6)
        with (images / 'Image.lz4boot').open('wb') as stream:
            stream.truncate(40 * 1024**2)
        with self.assertRaisesRegex(ValueError, '32 MiB'):
            recipe(images, dict(lz4=True, premerge=True, cutdown=True, ramdisk=True))


if __name__ == '__main__':
    unittest.main()

    def test_all_profiles_modes_and_input_versions_share_default_output(self):
        first = self.plan()
        for plan in (first, self.plan('fixture-empty'),
                     replace(first, development=True), replace(first, fingerprint='b' * 64)):
            self.assertEqual(workflow.output_for(self.ctx, plan), self.ctx.repo / '.output')
        self.assertEqual(workflow.output_for(self.ctx, first, self.root / 'override'),
                         self.root / 'override')

    def test_changed_inputs_keep_compiled_objects_and_repopulate_target(self):
        plan, _ = self.configure()
        objects = self.output / 'build/fixture-1'
        objects.mkdir(parents=True)
        (objects / 'kept.o').write_bytes(b'compiled')
        (objects / '.stamp_built').touch()
        (objects / '.stamp_target_installed').touch()
        (self.output / 'target/usr/bin').mkdir(parents=True)
        (self.output / 'target/usr/bin/removed-service').write_text('obsolete')
        changed = replace(plan, fingerprint='b' * 64)
        with patch.object(workflow, 'run', side_effect=self.fake_configure):
            workflow.configure(self.ctx, changed, self.output, False)
        workflow.assert_current(self.ctx, changed, self.output)
        workflow.reinstall_target(self.output)
        self.assertFalse((self.output / 'target').exists())
        self.assertFalse((objects / '.stamp_target_installed').exists())
        self.assertEqual((objects / 'kept.o').read_bytes(), b'compiled')
        self.assertTrue((objects / '.stamp_built').exists())
