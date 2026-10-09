"""Host CLI regressions: no builds, mounts, or real block-device writes."""
import contextlib
from datetime import datetime, timezone
from io import StringIO
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from src import builds, cli, images, snapshots
from src.common import Context, UserError, choose


class ToolsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.repo = self.root / 'repo with spaces'
        self.br = self.root / 'buildroot'
        (self.repo / 'configs').mkdir(parents=True)
        self.br.mkdir()
        (self.br / 'Makefile').write_text('export BR2_VERSION := test\n')
        (self.repo / 'build.toml').write_text('''schema = 1
[configure]
targets = ["{board}_defconfig"]
inputs = ["configs/{board}_defconfig"]
outputs = ["{output}/.config"]
[image]
targets = ["all"]
config = ["*"]
outputs = ["{output}/images/sdcard.img"]
[[steps]]
name = "probe"
when = "BR2_PACKAGE_POLIS_GPIO_TEST"
targets = ["polis-gpio-test-reconfigure"]
outputs = ["{output}/images/sdcard.img"]
[[steps]]
name = "firmware"
when = "BR2_PACKAGE_RPI_FIRMWARE"
targets = ["rpi-firmware-reinstall"]
inputs = ["firmware inputs"]
outputs = ["{output}/images/rpi-firmware"]
''')
        for board in ('polis_rpi4', 'polis_rpi4_musl', 'other'):
            (self.repo / 'configs' / f'{board}_defconfig').write_text('')
        self.ctx = Context(self.repo, self.br, self.br / 'dl', self.repo / 'snapshots')
        self.stdout = contextlib.redirect_stdout(StringIO())
        self.stdout.__enter__()

    def tearDown(self):
        self.stdout.__exit__(None, None, None)
        for path in self.root.rglob('*'):
            if not path.is_symlink():
                path.chmod(path.stat().st_mode | 0o700)
        self.tmp.cleanup()

    def output(self, board='polis_rpi4_musl'):
        output = self.ctx.output(board)
        output.mkdir(parents=True, exist_ok=True)
        (output / '.config').write_text(
            f'BR2_DEFCONFIG="{self.repo}/configs/{board}_defconfig"\n')
        return output

    def image(self, output):
        path = output / 'images/sdcard.img'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'original image')
        return path

    @staticmethod
    def disk(**values):
        return dict(name='/dev/test', type='disk', size=1000, ro=False,
                    mountpoints=[None], children=[], **values)

    def test_boards_discovered_no_special_output(self):
        self.assertEqual(self.ctx.boards, ['other', 'polis_rpi4', 'polis_rpi4_musl'])
        self.assertEqual(self.ctx.output('polis_rpi4'), self.repo / 'output/polis_rpi4')
        self.assertEqual(self.ctx.output('polis_rpi4', self.root / 'custom'), self.root / 'custom')

    def test_selection_has_no_default_even_for_one_choice(self):
        with patch('sys.stdin.isatty', return_value=True), patch('builtins.input', return_value=''):
            self.assertIsNone(choose(['only-board'], 'Board'))
        with patch('sys.stdin.isatty', return_value=False):
            self.assertIsNone(choose(['only-board'], 'Board'))

    def test_selection_is_explicit(self):
        with patch('sys.stdin.isatty', return_value=True), patch('builtins.input', return_value='2'):
            self.assertEqual(choose(['one', 'two'], 'Board'), 'two')
        with patch('sys.stdin.isatty', return_value=True), patch('builtins.input', return_value='0'):
            with self.assertRaises(UserError):
                choose(['one'], 'Board')

    def test_cli_without_arguments_never_builds(self):
        with patch.object(cli, 'REPO', self.repo), patch.object(builds, 'build') as build:
            self.assertEqual(cli.main([]), 0)
            build.assert_not_called()

    def test_build_without_board_lists_only_in_noninteractive_mode(self):
        with patch.object(cli, 'REPO', self.repo), patch('sys.stdin.isatty', return_value=False), \
                patch.dict(os.environ, {'BOARD': 'polis_rpi4_musl'}), \
                patch.object(builds, 'build') as build:
            self.assertEqual(cli.main(['build']), 0)
            build.assert_not_called()

    def test_configure_arguments_use_paths_without_shell(self):
        command = builds.build_command(self.ctx, 'configure', 'polis_rpi4')
        self.assertIn(f'O={self.repo}/output/polis_rpi4', command)
        self.assertIn(f'BR2_EXTERNAL={self.repo}', command)
        self.assertEqual(command[-1], 'polis_rpi4_defconfig')

    def test_wrong_board_or_unconfigured_output_rejected(self):
        for board in ('missing', 'other'):
            with self.assertRaises(UserError):
                builds.build_command(self.ctx, 'build', board)
        output = self.output()
        with self.assertRaises(UserError):
            builds.build_command(self.ctx, 'configure', 'other', output)

    def test_build_and_save_require_matching_configuration(self):
        self.output()
        self.assertEqual(builds.build_command(self.ctx, 'build', 'polis_rpi4_musl')[-1], 'all')
        self.assertEqual(builds.build_command(self.ctx, 'savedefconfig', 'polis_rpi4_musl')[-1],
                         f'BR2_DEFCONFIG={self.repo}/configs/polis_rpi4_musl_defconfig')

    def test_build_selects_once_configures_builds_and_snapshots_fresh_output(self):
        output = self.root / 'custom output'
        captures = self.root / 'custom snapshots'
        steps = []

        def run_build(command):
            self.assertIn(f'O={output}', command)
            self.assertIn(f'BR2_DL_DIR={self.root}/downloads', command)
            steps.append(command[-1])
            if command[-1] == 'polis_rpi4_musl_defconfig':
                output.mkdir(exist_ok=True)
                (output / '.config').write_text(
                    f'BR2_DEFCONFIG="{self.repo}/configs/polis_rpi4_musl_defconfig"\n')
            else:
                self.assertEqual(command[-1], 'all')
                self.image(output)

        with patch.object(cli, 'REPO', self.repo), \
                patch.object(cli, 'choose', return_value='polis_rpi4_musl') as choose_board, \
                patch.object(builds, 'run', side_effect=run_build):
            self.assertEqual(cli.main(['build', '--buildroot', str(self.br),
                                       '--downloads', str(self.root / 'downloads'),
                                       '--output', str(output), '--snapshots', str(captures)]), 0)
        choose_board.assert_called_once()
        self.assertEqual(steps, ['polis_rpi4_musl_defconfig', 'all'])
        saved, = captures.iterdir()
        self.assertRegex(saved.name, r'^polis_rpi4_musl-\d{8}$')
        self.assertEqual((saved / 'images/sdcard.img').read_bytes(), b'original image')
        self.assertFalse((saved / '.incomplete').exists())
        self.assertEqual((saved / 'images/sdcard.img').stat().st_mode & 0o222, 0)

    def test_automatic_snapshot_names_use_local_day_and_skip_existing_names(self):
        # UTC has rolled over, but the Chicago calendar day is still October 8.
        instant = datetime(2026, 10, 9, 1, tzinfo=timezone.utc)
        with patch.object(snapshots, 'datetime') as clock:
            clock.now.side_effect = lambda zone: instant.astimezone(zone)
            base = 'polis_rpi4_musl-20261008'
            self.assertEqual(snapshots.automatic_name(self.ctx, 'polis_rpi4_musl'), base)
            (self.ctx.snapshots / base).mkdir(parents=True)
            # Interrupted captures and dangling links must also reserve their names.
            (self.ctx.snapshots / base / '.incomplete').touch()
            self.assertEqual(snapshots.automatic_name(self.ctx, 'polis_rpi4_musl'), base + '-2')
            (self.ctx.snapshots / (base + '-2')).symlink_to(self.root / 'missing')
            self.assertEqual(snapshots.automatic_name(self.ctx, 'polis_rpi4_musl'), base + '-3')

    def test_build_failure_never_snapshots_stale_image(self):
        output = self.output()
        self.image(output)
        for failed_step in ('polis_rpi4_musl_defconfig', 'all'):
            steps = []

            def run_build(command):
                steps.append(command[-1])
                if command[-1] == failed_step:
                    raise subprocess.CalledProcessError(2, command)

            with self.subTest(failed_step=failed_step), \
                    patch.object(builds, 'run', side_effect=run_build), \
                    patch.object(snapshots, 'snapshot') as capture:
                with self.assertRaises(subprocess.CalledProcessError):
                    builds.build(self.ctx, 'build', 'polis_rpi4_musl', None)
                capture.assert_not_called()
                self.assertEqual(steps[-1], failed_step)

    def test_build_no_configure_preserves_config_and_uses_snapshot_name(self):
        output = self.output()
        self.image(output)
        original = (output / '.config').read_bytes()
        with patch.object(cli, 'REPO', self.repo), patch.object(builds, 'run') as run:
            self.assertEqual(cli.main(['build', '--board', 'polis_rpi4_musl',
                                       '--buildroot', str(self.br), '--no-configure',
                                       '--name', 'custom-build']), 0)
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args.args[0][-1], 'all')
        self.assertEqual((output / '.config').read_bytes(), original)
        self.assertTrue((self.ctx.snapshots / 'custom-build/images/sdcard.img').is_file())

    def test_build_rejects_snapshot_name_before_running(self):
        (self.ctx.snapshots / 'taken').mkdir(parents=True)
        for name in ('../bad', '', 'taken'):
            with self.subTest(name=name), patch.object(builds, 'run') as run:
                with self.assertRaises(UserError):
                    builds.build(self.ctx, 'build', 'polis_rpi4_musl', None, name=name)
                run.assert_not_called()

    def test_build_refreshes_enabled_local_package_before_image_and_snapshot(self):
        output = self.root / 'custom output'
        steps = []

        def run_build(command):
            self.assertIn(f'O={output}', command)
            self.assertIn(f'BR2_DL_DIR={self.ctx.downloads}', command)
            steps.append(command[-1])
            if command[-1] == 'polis_rpi4_musl_defconfig':
                output.mkdir(exist_ok=True)
                (output / '.config').write_text(
                    f'BR2_DEFCONFIG="{self.repo}/configs/polis_rpi4_musl_defconfig"\n'
                    'BR2_PACKAGE_POLIS_GPIO_TEST=y\n')
            elif command[-1] == 'all':
                self.image(output)

        with patch.object(builds, 'run', side_effect=run_build):
            builds.build(self.ctx, 'build', 'polis_rpi4_musl', output, name='refreshed')
        self.assertEqual(steps, ['polis_rpi4_musl_defconfig', 'polis-gpio-test-reconfigure', 'all'])
        self.assertTrue((self.ctx.snapshots / 'refreshed/images/sdcard.img').is_file())

    def test_no_configure_skips_unchanged_local_package_on_second_build(self):
        output = self.output()
        self.image(output)
        config = output / '.config'
        config.write_text(config.read_text() + 'BR2_PACKAGE_POLIS_GPIO_TEST=y\n')
        original = config.read_bytes()
        with patch.object(builds, 'run') as run, patch.object(snapshots, 'snapshot'):
            for _ in range(2):
                builds.build(self.ctx, 'build', 'polis_rpi4_musl', None, configure=False)
        self.assertEqual([call.args[0][-1] for call in run.call_args_list],
                         ['polis-gpio-test-reconfigure', 'all'])
        self.assertEqual(config.read_bytes(), original)

    def test_plain_build_picks_up_changed_firmware_files_before_image(self):
        output = self.root / 'custom output'
        firmware = self.repo / 'firmware inputs'
        firmware.mkdir()
        source = firmware / 'config.txt'
        cmdline = firmware / 'cmdline.txt'
        source.write_text('force_eeprom_read=1\n')
        cmdline.write_text('rootfstype=erofs\n')
        staged = output / 'images/rpi-firmware'
        steps = []

        def run_build(command):
            self.assertIn(f'O={output}', command)
            self.assertIn(f'BR2_DL_DIR={self.ctx.downloads}', command)
            steps.append(command[-1])
            if command[-1] == 'polis_rpi4_musl_defconfig':
                output.mkdir(parents=True, exist_ok=True)
                (output / '.config').write_text(
                    f'BR2_DEFCONFIG="{self.repo}/configs/polis_rpi4_musl_defconfig"\n'
                    'BR2_PACKAGE_POLIS_GPIO_TEST=y\nBR2_PACKAGE_RPI_FIRMWARE=y\n')
            elif command[-1] == 'rpi-firmware-reinstall':
                staged.mkdir(parents=True, exist_ok=True)
                (staged / 'config.txt').write_bytes(source.read_bytes())
                (staged / 'cmdline.txt').write_bytes(cmdline.read_bytes())
            elif command[-1] == 'all':
                # Assemble from staged files, just as genimage does.
                self.image(output).write_bytes((staged / 'config.txt').read_bytes()
                                               + (staged / 'cmdline.txt').read_bytes())

        with patch.object(builds, 'run', side_effect=run_build):
            builds.build(self.ctx, 'build', 'polis_rpi4_musl', output, name='before')
            source.write_text('force_eeprom_read=0\ndisable_poe_fan=1\n')
            cmdline.write_text('rootfstype=erofs ro console=null\n')
            builds.build(self.ctx, 'build', 'polis_rpi4_musl', output, name='after')
        self.assertEqual(steps, ['polis_rpi4_musl_defconfig', 'polis-gpio-test-reconfigure',
                                 'rpi-firmware-reinstall', 'all',
                                 'rpi-firmware-reinstall', 'all'])
        self.assertEqual((self.ctx.snapshots / 'after/images/sdcard.img').read_bytes(),
                         source.read_bytes() + cmdline.read_bytes())
        self.assertEqual((self.ctx.snapshots / 'before/images/sdcard.img').read_bytes(),
                         b'force_eeprom_read=1\nrootfstype=erofs\n')

    def test_no_configure_refreshes_firmware_but_preserves_configuration(self):
        output = self.output()
        self.image(output)
        config = output / '.config'
        config.write_text(config.read_text() + 'BR2_PACKAGE_RPI_FIRMWARE=y\n')
        original = config.read_bytes()
        with patch.object(builds, 'run') as run, patch.object(snapshots, 'snapshot'):
            builds.build(self.ctx, 'build', 'polis_rpi4_musl', None, configure=False)
        self.assertEqual([call.args[0][-1] for call in run.call_args_list],
                         ['rpi-firmware-reinstall', 'all'])
        self.assertEqual(config.read_bytes(), original)

    def test_firmware_refresh_failure_prevents_image_and_snapshot(self):
        output = self.output()
        self.image(output)
        config = output / '.config'
        config.write_text(config.read_text() + 'BR2_PACKAGE_RPI_FIRMWARE=y\n')
        with patch.object(builds, 'run', side_effect=subprocess.CalledProcessError(2, 'make')) as run, \
                patch.object(snapshots, 'snapshot') as capture:
            with self.assertRaises(subprocess.CalledProcessError):
                builds.build(self.ctx, 'build', 'polis_rpi4_musl', None, configure=False)
        self.assertEqual(run.call_args.args[0][-1], 'rpi-firmware-reinstall')
        run.assert_called_once()
        capture.assert_not_called()

    def test_local_package_failure_prevents_image_and_snapshot(self):
        output = self.output()
        self.image(output)
        config = output / '.config'
        config.write_text(config.read_text() + 'BR2_PACKAGE_POLIS_GPIO_TEST=y\n')
        with patch.object(builds, 'run', side_effect=subprocess.CalledProcessError(2, 'make')) as run, \
                patch.object(snapshots, 'snapshot') as capture:
            with self.assertRaises(subprocess.CalledProcessError):
                builds.build(self.ctx, 'build', 'polis_rpi4_musl', None, configure=False)
        self.assertEqual(run.call_args.args[0][-1], 'polis-gpio-test-reconfigure')
        run.assert_called_once()
        capture.assert_not_called()

    def test_build_cancellation_never_configures_or_snapshots(self):
        with patch.object(cli, 'REPO', self.repo), patch.object(cli, 'choose', return_value=None), \
                patch.object(builds, 'run') as run, patch.object(snapshots, 'snapshot') as capture:
            self.assertEqual(cli.main(['build']), 0)
            run.assert_not_called()
            capture.assert_not_called()

    def test_build_snapshot_failure_is_reported(self):
        self.output()
        with patch.object(cli, 'REPO', self.repo), patch.object(builds, 'run'), \
                patch.object(snapshots, 'snapshot', side_effect=OSError('copy failed')), \
                contextlib.redirect_stderr(StringIO()) as errors:
            self.assertEqual(cli.main(['build', '--board', 'polis_rpi4_musl']), 1)
        self.assertIn('copy failed', errors.getvalue())

    def test_image_discovery_deduplicates_and_ignores_partial_captures(self):
        image = self.image(self.output())
        saved = snapshots.snapshot(self.ctx, image.parent.parent, 'complete')
        self.image(self.ctx.snapshots / 'incomplete')
        (self.ctx.snapshots / 'incomplete/.incomplete').touch()
        self.image(self.ctx.snapshots / 'bare-image')
        empty = self.image(self.output('other'))
        empty.write_bytes(b'')
        self.assertEqual(images.discover_images(self.ctx, image.parent.parent),
                         sorted([image, saved / 'images/sdcard.img']))

    def test_image_discovery_only_searches_board_outputs_and_explicit_override(self):
        image = self.image(self.output())
        old = self.image(self.repo / 'output/rpi4')
        self.image(self.repo / 'output')
        self.image(self.br / 'output')
        self.assertEqual(images.discover_images(self.ctx), [image])
        self.assertEqual(images.discover_images(self.ctx, old.parent.parent), sorted([image, old]))

    def test_disk_refusals_include_nested_mounts_and_swap(self):
        node = self.disk()
        images.validate_disk(node, 1000)
        cases = [dict(node, type='part'), dict(node, ro=True), dict(node, size=9),
                 dict(node, children=[{'mountpoints': ['/'], 'children': []}]),
                 dict(node, children=[{'mountpoints': [None], 'children': [
                     {'mountpoints': ['[SWAP]']}]}])]
        for bad in cases:
            with self.subTest(bad=bad), self.assertRaises(UserError):
                images.validate_disk(bad, 10)

    def test_regular_file_is_not_a_disk(self):
        with self.assertRaisesRegex(UserError, 'block device'):
            images.inspect_destination(self.image(self.output()), 10)

    def test_flash_noninteractive_never_runs_writer(self):
        with patch('sys.stdin.isatty', return_value=False), patch.object(images, 'run') as run:
            with self.assertRaises(UserError):
                images.flash(self.ctx, self.image(self.output()), Path('/dev/test'))
            run.assert_not_called()

    def test_flash_picker_only_offers_completed_snapshots(self):
        image = self.image(self.output())
        saved = snapshots.snapshot(self.ctx, image.parent.parent, 'complete')
        self.image(self.ctx.snapshots / 'incomplete')
        (self.ctx.snapshots / 'incomplete/.incomplete').touch()
        self.image(self.ctx.snapshots / 'bare-image')
        with patch.object(images, 'choose', return_value=None) as choose, \
                patch.object(images, 'run') as run:
            images.flash(self.ctx, None, None)
        choose.assert_called_once_with([saved / 'images/sdcard.img'], 'Image')
        run.assert_not_called()

    def test_flash_without_snapshots_does_not_fall_back_to_output(self):
        self.image(self.output())
        with patch.object(images, 'choose', return_value=None) as choose, \
                patch.object(images, 'run') as run:
            images.flash(self.ctx, None, None)
        choose.assert_called_once_with([], 'Image')
        run.assert_not_called()

    def test_flash_rejects_output_option(self):
        with contextlib.redirect_stderr(StringIO()), self.assertRaises(SystemExit):
            cli.parser().parse_args(['flash', '--output', str(self.output())])

    def test_flash_only_displays_and_offers_removable_whole_disks(self):
        image = self.image(self.output())
        card = dict(self.disk(rm=True), name='/dev/sda', tran='usb',
                    children=[dict(name='/dev/sda1', type='part', rm=True,
                                   mountpoints=['/run/media/bootfs'])])
        internal = dict(self.disk(rm=False), name='/dev/nvme0n1', tran='nvme')
        fixed_usb = dict(self.disk(rm=False), name='/dev/sdb', tran='usb')
        partition = dict(self.disk(rm=True), name='/dev/sdc1', type='part')
        native_card = dict(self.disk(rm=True), name='/dev/mmcblk0', tran='mmc')
        with patch('sys.stdin.isatty', return_value=True), \
                patch.object(images, 'disks', return_value=[card, internal, fixed_usb, partition,
                                                          native_card, self.disk()]), \
                patch.object(images, 'show_disks') as show, \
                patch.object(images, 'choose', return_value=None) as choose, \
                patch.object(images, 'run') as run:
            images.flash(self.ctx, image, None)
        show.assert_called_once_with([card, native_card])
        choose.assert_called_once_with(['/dev/sda', '/dev/mmcblk0'], 'Device')
        run.assert_not_called()

    def test_flash_without_removable_disks_offers_no_destination(self):
        image = self.image(self.output())
        with patch('sys.stdin.isatty', return_value=True), \
                patch.object(images, 'disks', return_value=[self.disk(rm=False)]), \
                patch.object(images, 'show_disks') as show, \
                patch.object(images, 'choose', return_value=None) as choose, \
                patch.object(images, 'run') as run:
            images.flash(self.ctx, image, None)
        show.assert_called_once_with([])
        choose.assert_called_once_with([], 'Device')
        run.assert_not_called()

    def flash_setup(self):
        image = self.image(self.output())
        # The fake destination is a regular file. inspect_destination is mocked;
        # no real block device or privilege escalation is ever used by these tests.
        device = self.root / 'fake device'
        device.touch()
        return image, device

    def test_cancel_never_writes(self):
        image, device = self.flash_setup()
        with patch('sys.stdin.isatty', return_value=True), patch.object(images, 'disks', return_value=[]), \
                patch.object(images, 'inspect_destination', return_value=(1, 'serial', 1000)), \
                patch('builtins.input', return_value='no'), patch.object(images, 'run') as run:
            with self.assertRaisesRegex(UserError, 'Cancelled'):
                images.flash(self.ctx, image, device)
            run.assert_not_called()

    def test_confirmed_flash_uses_exact_paths_and_flushes(self):
        image, device = self.flash_setup()
        with patch('sys.stdin.isatty', return_value=True), patch.object(images, 'disks', return_value=[]), \
                patch.object(images, 'inspect_destination', return_value=(1, 'serial', 1000)) as inspect, \
                patch('builtins.input', return_value=str(device)), patch('os.geteuid', return_value=0), \
                patch.object(images, 'run') as run:
            images.flash(self.ctx, image, device)
            self.assertEqual(inspect.call_count, 2)
            run.assert_called_once_with(['dd', f'if={image}', f'of={device}',
                                         'bs=4M', 'conv=fsync', 'status=progress'])

    def test_destination_changed_after_confirmation_never_writes(self):
        image, device = self.flash_setup()
        with patch('sys.stdin.isatty', return_value=True), patch.object(images, 'disks', return_value=[]), \
                patch.object(images, 'inspect_destination', side_effect=[(1, 'a', 100), (2, 'b', 100)]), \
                patch('builtins.input', return_value=str(device)), patch('os.geteuid', return_value=0), \
                patch.object(images, 'run') as run:
            with self.assertRaisesRegex(UserError, 'Destination changed'):
                images.flash(self.ctx, image, device)
            run.assert_not_called()

    def test_snapshot_preserves_independent_read_only_copies(self):
        output = self.output()
        image = self.image(output)
        saved = snapshots.snapshot(self.ctx, output, 'baseline')
        image.write_bytes(b'rebuilt')
        self.assertEqual((saved / 'images/sdcard.img').read_bytes(), b'original image')
        self.assertFalse((saved / '.incomplete').exists())
        self.assertEqual((saved / 'images/sdcard.img').stat().st_mode & 0o222, 0)
        self.assertNotEqual(image.stat().st_ino, (saved / 'images/sdcard.img').stat().st_ino)
        subprocess.run(['sha256sum', '-c', '--quiet', 'SHA256SUMS'], cwd=saved, check=True)
        with self.assertRaises(UserError):
            snapshots.snapshot(self.ctx, output, 'baseline')

    def test_failed_snapshot_retains_incomplete_marker(self):
        output = self.output()
        self.image(output)
        with patch.object(snapshots.shutil, 'copytree', side_effect=OSError('copy failed')):
            with self.assertRaises(OSError):
                snapshots.snapshot(self.ctx, output, 'failed')
        self.assertTrue((self.ctx.snapshots / 'failed/.incomplete').exists())

    def test_source_rewritten_during_copy_rejected(self):
        output = self.output()
        self.image(output)
        with patch.object(snapshots, 'digest', side_effect=['old', 'new']):
            with self.assertRaisesRegex(UserError, 'Source image changed'):
                snapshots.snapshot(self.ctx, output, 'changed')
        self.assertTrue((self.ctx.snapshots / 'changed/.incomplete').exists())

    def test_snapshot_requires_config_inside_selected_output(self):
        output = self.br / 'output'
        self.image(output)
        (self.br / '.config').write_text('BR2_DEFCONFIG=""\n')
        with self.assertRaisesRegex(UserError, 'Missing'):
            snapshots.snapshot(self.ctx, output, 'stock')
        self.assertFalse(self.ctx.snapshots.exists())

    def test_snapshot_name_validation(self):
        output = self.output()
        self.image(output)
        for name in ('../bad', '', '/absolute'):
            with self.assertRaises(UserError):
                snapshots.snapshot(self.ctx, output, name)

    def test_standalone_snapshot_uses_build_naming_and_checks_board(self):
        output = self.output()
        self.image(output)
        with patch.object(cli, 'REPO', self.repo), patch.object(builds, 'run') as run:
            args = ['snapshot', '--board', 'polis_rpi4_musl', '--buildroot', str(self.br)]
            self.assertEqual(cli.main(args), 0)
            self.assertEqual(cli.main(args), 0)
            names = snapshots.completed(self.ctx)
            self.assertEqual(len(names), 2)
            self.assertRegex(names[0], r'^polis_rpi4_musl-\d{8}$')
            self.assertEqual(names[1], names[0] + '-2')
            with contextlib.redirect_stderr(StringIO()) as errors:
                self.assertEqual(cli.main(['snapshot', '--board', 'other',
                                           '--output', str(output)]), 1)
            self.assertIn('another configuration', errors.getvalue())
            run.assert_not_called()

    def test_standalone_snapshot_rejects_source_output_option(self):
        with contextlib.redirect_stderr(StringIO()), self.assertRaises(SystemExit):
            cli.parser().parse_args(['snapshot', '--source-output', str(self.br / 'output')])


if __name__ == '__main__':
    unittest.main()
