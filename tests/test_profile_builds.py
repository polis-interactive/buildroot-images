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
from src.profiles.image import recipe
from src.profiles.options import BASE_GROUPS, Capability, Group

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
        self.ctx = Context(REPO, br, br / 'dl', self.root / '.snapshots')
        self.output = self.root / '.output'
        manifest = patch.object(resolver, 'input_manifest', return_value=[
            dict(root='repo', path='profiles/polis-rpi4-speed.toml', sha256='a' * 64)])
        manifest.start()
        self.addCleanup(manifest.stop)
        self.enterContext(patch.object(inputs, 'verify_inputs', return_value=
                                      json.loads((REPO / inputs.LOCK_PATH).read_text())))

    def plan(self, name='polis-rpi4-speed', groups=None, requires=None):
        if groups is None and requires is None:
            return resolver.resolve(self.ctx, name)
        profile = resolver.load_profile(REPO / 'profiles' / (name + '.toml'))
        if groups is not None:
            profile = replace(profile, groups=tuple(groups))
        if requires is not None:
            profile = replace(profile, requires=tuple(requires))
        with patch.object(resolver, 'load_profile', return_value=profile):
            return resolver.resolve(self.ctx, name)

    def test_every_enum_has_a_valid_manifest(self):
        for group in Group:
            self.assertEqual(resolver.load_group(REPO, group)['id'], group.value)

    def test_base_has_no_optional_features_or_restrictions(self):
        p = self.plan('polis-rpi4-base')
        self.assertEqual(set(p.groups), {g.value for g in BASE_GROUPS})
        self.assertNotIn('gpu_mem', p.values['firmware'])
        self.assertNotIn('CONFIG_DRM', p.values['kernel'])
        br = resolver.write_generated(self.ctx, p, self.output)
        for name in ('BR2_TOOLCHAIN_EXTERNAL_BOOTLIN_AARCH64_MUSL_STABLE',
                     'BR2_TARGET_ROOTFS_EROFS', 'BR2_POLIS_PREFER_STATIC'):
            self.assertEqual(br[name], 'y')
        self.assertEqual(p.values['busybox']['CONFIG_STATIC'], 'y')
        self.assertNotIn('BR2_PACKAGE_POLIS_BOOT_DIAGNOSTICS', br)
        self.assertNotIn('BR2_POLIS_KERNEL_ENTRY', br)
        self.assertNotIn('BR2_PACKAGE_IWD', br)
        image = json.loads((self.output / 'profile/image-plan.json').read_text())
        self.assertFalse(any(image[k] for k in ('lz4', 'cutdown', 'premerge', 'ramdisk', 'eeprom')))

    def test_speed_selects_all_optimizations_and_eeprom_settings(self):
        p = self.plan()
        resolver.write_generated(self.ctx, p, self.output)
        image = json.loads((self.output / 'profile/image-plan.json').read_text())
        self.assertTrue(all(image[k] for k in ('lz4', 'cutdown', 'premerge', 'ramdisk', 'eeprom')))
        self.assertEqual(p.values['firmware']['gpu_mem'], '16')
        self.assertEqual(p.values['buildroot']['BR2_PACKAGE_RPI_FIRMWARE_VARIANT_PI4_CD'], 'y')
        firmware = (self.output / 'profile/config.txt').read_text()
        self.assertNotIn('dtoverlay=', firmware)
        self.assertNotIn('start_file=', firmware)
        self.assertEqual(workflow.new_audit(p)['hardware'][0]['expected'], '0')
        self.assertEqual(p.values['kernel']['CONFIG_QUOTA'], 'y')
        self.assertEqual(p.values['kernel']['CONFIG_EXT4_FS'], 'y')

    def test_wifi_profile_is_non_development_and_base_stays_minimal(self):
        wifi = self.plan('polis-rpi4-wifi')
        self.assertFalse(wifi.development)
        self.assertNotIn('development-access', wifi.groups)
        self.assertNotIn('headless', wifi.groups)
        self.assertEqual(wifi.values['buildroot']['BR2_PACKAGE_POLIS_FIRSTBOOT'], 'y')
        for symbol in ('BR2_PACKAGE_IWD', 'BR2_PACKAGE_POLIS_WIFI_SETUPD',
                       'BR2_PACKAGE_AVAHI_DAEMON'):
            self.assertEqual(wifi.values['buildroot'][symbol], 'y')
            self.assertNotIn(symbol, self.plan('polis-rpi4-base').values['buildroot'])
        self.assertNotIn('BR2_PACKAGE_DROPBEAR', wifi.values['buildroot'])
        self.assertEqual(wifi.values['buildroot']['BR2_TARGET_GENERIC_HOSTNAME'], 'polis')
        self.assertEqual(wifi.values['kernel']['CONFIG_ETHERNET'], 'n')
        self.assertIn('board/rpi4/install-wifi.py', wifi.post_build)
        self.assertNotIn('board/rpi4/install-development.py', wifi.post_build)
        self.assertNotIn('etc/init.d/S90dev-app', wifi.values['rootfs'])

    def test_wifi_password_is_a_reviewed_input_without_plaintext_in_audits(self):
        with patch.dict(os.environ, POLIS_WIFI_SETUP_PASSWORD='image-password123'):
            first = self.plan('polis-rpi4-wifi')
            base = self.plan('polis-rpi4-base')
        with patch.dict(os.environ, POLIS_WIFI_SETUP_PASSWORD='different-password456'):
            second = self.plan('polis-rpi4-wifi')
            self.assertEqual(base.fingerprint, self.plan('polis-rpi4-base').fingerprint)
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
        plan = replace(self.plan('polis-rpi4-wifi'), wifi_password='')
        with patch.object(workflow, 'run') as run:
            with self.assertRaisesRegex(UserError, 'POLIS_WIFI_SETUP_PASSWORD'):
                workflow.configure(self.ctx, plan, self.output, False)
            run.assert_not_called()

    def test_speed_excludes_wifi_services_firmware_and_kernel_drivers(self):
        speed = self.plan()
        for symbol in ('BR2_PACKAGE_IWD', 'BR2_PACKAGE_POLIS_WIFI_SETUPD',
                       'BR2_PACKAGE_BRCMFMAC_SDIO_FIRMWARE_RPI_WIFI',
                       'BR2_PACKAGE_AVAHI', 'BR2_PACKAGE_DBUS', 'BR2_PACKAGE_OPENRESOLV'):
            self.assertEqual(speed.values['buildroot'][symbol], 'n')
        for symbol in ('CONFIG_WLAN', 'CONFIG_CFG80211', 'CONFIG_MAC80211', 'CONFIG_BRCMFMAC'):
            self.assertEqual(speed.values['kernel'][symbol], 'n')
        self.assertNotIn('etc/modules-load.d/wifi.conf', speed.values['rootfs'])
        self.assertNotIn('board/rpi4/install-wifi.py', speed.post_build)
        with self.assertRaisesRegex(UserError, 'wifi.*disabled'):
            self.plan(requires=[Capability.WIFI])

    def test_dev_includes_wifi_portal_plus_ethernet_and_ssh(self):
        dev = self.plan('polis-rpi4-dev')
        wifi = self.plan('polis-rpi4-wifi')
        for symbol, value in wifi.values['buildroot'].items():
            if symbol.startswith('BR2_PACKAGE_') and value == 'y':
                self.assertEqual(dev.values['buildroot'].get(symbol), 'y', symbol)
        self.assertEqual(dev.values['buildroot']['BR2_PACKAGE_DROPBEAR'], 'y')
        self.assertEqual(dev.values['kernel']['CONFIG_BCMGENET'], 'y')
        self.assertEqual(dev.values['buildroot']['BR2_TARGET_GENERIC_HOSTNAME'], 'polis-dev')

    def test_group_order_cannot_change_generated_output_or_fingerprint(self):
        p = self.plan()
        reverse = self.plan(groups=reversed(p.profile.groups))
        self.assertEqual(p.fingerprint, reverse.fingerprint)
        self.assertEqual(p.values, reverse.values)
        self.assertEqual(p.overlays, reverse.overlays)

    def test_development_mode_is_isolated_and_disables_provisioning(self):
        final = self.plan()
        dev = resolver.resolve(self.ctx, 'polis-rpi4-speed', development=True)
        self.assertNotEqual(final.fingerprint, dev.fingerprint)
        self.assertNotEqual(workflow.output_for(self.ctx, final), workflow.output_for(self.ctx, dev))
        self.assertEqual(final.values['buildroot']['BR2_PACKAGE_POLIS_FIRSTBOOT'], 'y')
        self.assertNotIn('BR2_PACKAGE_POLIS_FIRSTBOOT', dev.values['buildroot'])
        resolver.write_generated(self.ctx, dev, self.output)
        image = json.loads((self.output / 'profile/image-plan.json').read_text())
        self.assertTrue(image['development'])
        self.assertIn('bootloader_update=0', (self.output / 'profile/config.txt').read_text())
        self.configure()
        with self.assertRaisesRegex(UserError, 'another input set'):
            workflow.configure(self.ctx, dev, self.output, False)

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
                self.plan()

    def test_disabling_cutdown_restores_standard_firmware(self):
        p = self.plan(groups=[g for g in self.plan().profile.groups if g is not Group.CUTDOWN_FIRMWARE])
        resolver.write_generated(self.ctx, p, self.output)
        firmware = (self.output / 'profile/config.txt').read_text()
        self.assertIn('start_file=start4.elf', firmware)
        self.assertNotIn('gpu_mem=16', firmware)

    def test_disabling_premerge_restores_overlay_directives(self):
        p = self.plan(groups=[g for g in self.plan().profile.groups if g is not Group.PREMERGE_OVERLAYS])
        resolver.write_generated(self.ctx, p, self.output)
        firmware = (self.output / 'profile/config.txt').read_text()
        self.assertIn('dtoverlay=disable-bt', firmware)
        self.assertIn('dtoverlay=dwc2,dr_mode=peripheral', firmware)
        self.assertNotIn('BR2_PACKAGE_HOST_POLIS_DTMERGE', p.values['buildroot'])

    def test_premerge_without_collector_does_not_select_target_diagnostic_tools(self):
        p = self.plan(groups=[g for g in self.plan().profile.groups if g is not Group.BOOT_DIAGNOSTICS],
                      requires=[])
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
        self.assertIn('gpu_mem', staged)
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

    def test_every_profile_checks_the_shared_lock_before_configuration(self):
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
            workflow.execute(self.ctx, 'image', 'polis-rpi4-speed', self.output, 'speed',
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
            provision.prepare(self.ctx, 'polis-rpi4-base')
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
