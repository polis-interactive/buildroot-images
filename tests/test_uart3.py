"""Application UART isolation and ordered firmware-overlay packaging."""
from dataclasses import replace
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from src.common import Context, UserError
from src.profiles import resolver, workflow
from src.profiles.image import assemble, recipe
from src.profiles.options import Group
from profile_fixtures import PREMERGE_GROUPS

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('premerge', REPO / 'board/rpi4/premerge-dtb.py')
premerge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(premerge)


class Uart3Tests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        br = self.root / 'buildroot'
        (br / 'configs').mkdir(parents=True)
        (br / 'Makefile').write_text('BR2_VERSION_EPOCH = 1788535000\n')
        shutil.copyfile(REPO / 'profiles/base/upstream_defconfig',
                        br / 'configs/raspberrypi4_64_defconfig')
        self.ctx = Context(REPO, br, br / 'dl', self.root / 'snapshots')
        self.enterContext(patch.object(resolver, 'input_manifest', return_value=[]))

    def test_both_dev_profiles_require_dtoverlay_library_and_runtime_kernel_support(self):
        for name in ('polis-rpi4-dev', 'polis-rpi4-screen-dev'):
            with self.subTest(profile=name):
                plan = resolver.resolve(self.ctx, name, development=True)
                for key, value in (
                    ('BR2_PACKAGE_POLIS_DTOVERLAY', 'y'),
                    ('BR2_PACKAGE_DTC', 'y'),
                    ('BR2_PACKAGE_DTC_PROGRAMS', 'n'),
                    ('BR2_PACKAGE_RPI_FIRMWARE_INSTALL_DTB_OVERLAYS', 'y'),
                    ('BR2_LINUX_KERNEL_DTB_OVERLAY_SUPPORT', 'y'),
                ):
                    requested = plan.values['buildroot']
                    self.assertEqual(requested[key], value)
                    with self.assertRaisesRegex(UserError, key):
                        workflow.check_values(requested,
                                              dict(requested, **{key: 'n' if value == 'y' else 'y'}),
                                              'Buildroot', REPO, self.root)
                for key in ('CONFIG_OF_DYNAMIC', 'CONFIG_OF_OVERLAY',
                            'CONFIG_OF_CONFIGFS', 'CONFIG_CONFIGFS_FS'):
                    requested = plan.values['kernel']
                    self.assertEqual(requested[key], 'y')
                    with self.assertRaisesRegex(UserError, key):
                        workflow.check_values(requested, dict(requested, **{key: 'n'}),
                                              'kernel', REPO, self.root)
        for name in ('polis-rpi4-base', 'polis-rpi4-speed'):
            plan = resolver.resolve(self.ctx, name, development=True)
            self.assertNotIn('BR2_PACKAGE_POLIS_DTOVERLAY', plan.values['buildroot'])
            self.assertNotIn('BR2_PACKAGE_DTC', plan.values['buildroot'])

    def test_screen_adds_only_uart3_without_target_packages_or_console_changes(self):
        plan = resolver.resolve(self.ctx, 'polis-rpi4-screen-dev', development=True)
        profile = replace(plan.profile, groups=tuple(
            group for group in plan.profile.groups if group != Group.RPI4_UART3))
        with patch.object(resolver, 'load_profile', return_value=profile):
            before = resolver.resolve(self.ctx, profile.name, development=True)
        for domain in ('buildroot', 'busybox', 'cmdline', 'rootfs'):
            self.assertEqual(plan.values[domain], before.values[domain])
        self.assertEqual(plan.values['firmware'],
                         dict(before.values['firmware'], **{'overlay:uart3': 'y'}))
        self.assertEqual(plan.values['kernel'],
                         dict(before.values['kernel'], CONFIG_SERIAL_AMBA_PL011='y'))
        self.assertEqual(plan.values['cmdline']['console'], 'ttyAMA0,115200')
        inittab = REPO / 'profiles/groups/headless/rootfs-overlay/etc/inittab'
        self.assertFalse(any('getty' in line for line in inittab.read_text().splitlines()
                             if line and not line.startswith('#')))
        for name in ('polis-rpi4-dev', 'polis-rpi4-speed'):
            other = resolver.resolve(self.ctx, name, development=True)
            self.assertNotIn('overlay:uart3', other.values['firmware'])

    def test_normal_image_keeps_uart3_overlay_and_checks_required_config(self):
        plan = resolver.resolve(self.ctx, 'polis-rpi4-screen-dev', development=True)
        resolver.write_generated(self.ctx, plan, self.root)
        firmware = (self.root / 'profile/config.txt').read_text()
        self.assertEqual(firmware.count('dtoverlay=uart3\n'), 1)
        self.assertIn('dtoverlay=miniuart-bt\n', firmware)
        self.assertNotIn('ctsrts', firmware)
        self.assertNotIn('rs485', firmware)
        image = json.loads((self.root / 'profile/image-plan.json').read_text())
        self.assertEqual(image['firmware_overlays'], [
            'miniuart-bt', 'vc4-kms-v3d,cma-256', 'tc358743,media-controller',
            'tc358743-audio', 'uart3'])
        text, _ = recipe(self.root / 'images', image)
        self.assertIn('file overlays { image = "rpi-firmware/overlays" }', text)
        for domain, key in (
            ('buildroot', 'BR2_PACKAGE_RPI_FIRMWARE_INSTALL_DTB_OVERLAYS'),
            ('buildroot', 'BR2_LINUX_KERNEL_DTB_OVERLAY_SUPPORT'),
            ('kernel', 'CONFIG_SERIAL_AMBA_PL011'),
        ):
            requested = plan.values[domain]
            self.assertEqual(requested[key], 'y')
            with self.assertRaisesRegex(UserError, key):
                workflow.check_values(requested, dict(requested, **{key: 'n'}),
                                      domain, REPO, self.root)

    def test_premerge_passes_every_selected_overlay_once_in_order(self):
        profile = resolver.load_profile(REPO / 'profiles/polis-rpi4-screen-dev.toml')
        # The shipped screen profile rejects USB diagnostics; use a compatible
        # synthetic composition to exercise video + UART3 + existing premerge.
        profile = replace(profile, groups=(*PREMERGE_GROUPS, Group.RPI4_VIDEO,
                                           Group.RPI4_UART3), requires=())
        with patch.object(resolver, 'load_profile', return_value=profile):
            plan = resolver.resolve(self.ctx, profile.name, development=True)
        resolver.write_generated(self.ctx, plan, self.root)
        image = json.loads((self.root / 'profile/image-plan.json').read_text())
        overlays = image['firmware_overlays']
        self.assertIn('uart3', overlays)
        self.assertIn('disable-bt', overlays)
        self.assertIn('dwc2,dr_mode=peripheral', overlays)
        self.assertLess(overlays.index('tc358743,media-controller'),
                        overlays.index('tc358743-audio'))
        self.assertNotIn('dtoverlay=', (self.root / 'profile/config.txt').read_text())
        text, _ = recipe(self.root / 'images', image)
        self.assertNotIn('file overlays', text)
        self.assertIn('image = "bcm2711-rpi-4-b.premerged.dtb"', text)
        (self.root / 'images').mkdir()
        with patch('src.profiles.image.subprocess.run') as run:
            assemble(dict(BASE_DIR=str(self.root), BR2_EXTERNAL_POLIS_PATH=str(REPO),
                          BINARIES_DIR=str(self.root / 'images'),
                          HOST_DIR=str(self.root / 'host'), BUILD_DIR=str(self.root / 'build')))
        self.assertEqual(run.call_args_list[0].args[0][6:], overlays)


class PremergeTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.images, self.build, self.host = [self.root / name for name in ('images', 'build', 'host')]
        for path in (self.images / 'rpi-firmware/overlays', self.build, self.host / 'bin'):
            path.mkdir(parents=True)
        (self.host / 'bin/dtmerge').touch()
        (self.images / 'bcm2711-rpi-4-b.dtb').write_text('base')
        self.output = self.images / 'merged.dtb'

    def test_merger_chains_outputs_and_preserves_overlay_parameters(self):
        selections = ['disable-bt', 'uart3', 'dwc2,dr_mode=peripheral',
                      'tc358743,media-controller', 'tc358743-audio']
        for selection in selections:
            (self.images / 'rpi-firmware/overlays' / (selection.split(',')[0] + '.dtbo')).touch()
        def merge(args, **kwargs):
            self.assertTrue(kwargs['check'])
            Path(args[2]).write_text(Path(args[1]).read_text() + '\n' +
                                     ','.join([Path(args[3]).stem, *args[4:]]))
        with patch.object(premerge.subprocess, 'run', side_effect=merge) as run:
            premerge.merge(self.images, self.output, self.build, self.host, selections)
        self.assertEqual(run.call_count, len(selections))
        self.assertEqual(self.output.read_text().splitlines(), ['base', *selections])

    def test_missing_overlay_and_merge_failure_do_not_publish_output(self):
        with self.assertRaisesRegex(SystemExit, 'Missing selected firmware overlay'):
            premerge.merge(self.images, self.output, self.build, self.host, ['uart3'])
        self.assertFalse(self.output.exists())
        (self.images / 'rpi-firmware/overlays/uart3.dtbo').touch()
        with patch.object(premerge.subprocess, 'run', side_effect=subprocess.CalledProcessError(1, 'dtmerge')):
            with self.assertRaises(subprocess.CalledProcessError):
                premerge.merge(self.images, self.output, self.build, self.host, ['uart3'])
        self.assertFalse(self.output.exists())


if __name__ == '__main__':
    unittest.main()
