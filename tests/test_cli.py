"""Profile CLI, snapshots, and confirmed flashing without device writes."""
import contextlib
from io import StringIO
import json
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from src import cli
from src.artifacts import images, snapshots
from src.common import Context, UserError, choose


class ToolsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.repo = self.root / 'repo with spaces'
        self.br = self.root / 'buildroot'
        (self.repo / 'profiles').mkdir(parents=True)
        self.br.mkdir()
        (self.br / 'Makefile').write_text('export BR2_VERSION := test\n')
        for name in ('polis-rpi4-base', 'polis-rpi4-speed'):
            (self.repo / 'profiles' / f'{name}.toml').write_text(
                f'schema = 1\nname = "{name}"\nbase = "polis-rpi4-v1"\nrequires = []\ngroups = []\n')
        self.ctx = Context(self.repo, self.br, self.br / 'dl', self.repo / 'snapshots')
        self.stdout = contextlib.redirect_stdout(StringIO())
        self.stdout.__enter__()

    def tearDown(self):
        self.stdout.__exit__(None, None, None)
        for path in self.root.rglob('*'):
            if not path.is_symlink():
                path.chmod(path.stat().st_mode | 0o700)
        self.tmp.cleanup()

    def output(self, profile='polis-rpi4-speed'):
        output = self.repo / 'output' / (profile + '-0123456789ab')
        (output / 'profile').mkdir(parents=True, exist_ok=True)
        (output / 'profile/defconfig').write_text('BR2_aarch64=y\n')
        (output / '.config').write_text(f'BR2_DEFCONFIG="{output}/profile/defconfig"\n')
        (output / 'profile/audit.json').write_text(json.dumps(dict(profile=profile, state='built')))
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

    def test_profile_listing_and_help_only_expose_profiles(self):
        with patch.object(cli, 'REPO', self.repo), contextlib.redirect_stdout(StringIO()) as output:
            self.assertEqual(cli.main(['profiles']), 0)
            self.assertEqual(output.getvalue().splitlines(), self.ctx.profiles)
        with patch.object(cli, 'REPO', self.repo), contextlib.redirect_stdout(StringIO()) as output:
            self.assertEqual(cli.main([]), 0)
            self.assertEqual(output.getvalue().split('Available profiles:\n')[1].split(), self.ctx.profiles)
            self.assertNotIn('menuconfig', output.getvalue())
            self.assertNotIn('boards', output.getvalue())

    def test_removed_commands_and_options_are_rejected(self):
        for args in (['boards'], ['menuconfig'], ['savedefconfig'], ['linux-reconfigure'],
                     ['busybox-reconfigure'], ['build', '--board', 'polis_rpi4'],
                     ['build', '--no-configure'], ['build', '--name', 'test']):
            with self.subTest(args=args), contextlib.redirect_stderr(StringIO()), self.assertRaises(SystemExit):
                cli.parser().parse_args(args)

    def test_profile_actions_share_one_workflow(self):
        for action in ('configure', 'build', 'image', 'snapshot', 'audit'):
            with self.subTest(action=action), patch.object(cli, 'REPO', self.repo), \
                    patch.object(cli.profile_workflow, 'execute') as execute:
                self.assertEqual(cli.main([action, '--profile', 'polis-rpi4-speed']), 0)
                self.assertEqual(execute.call_args.args[1:3], (action, 'polis-rpi4-speed'))

    def test_eeprom_stage_uses_the_profile_output_override(self):
        with patch.object(cli, 'REPO', self.repo), patch.object(cli.profile_workflow, 'execute') as execute:
            self.assertEqual(cli.main(['prepare-eeprom', '--profile', 'polis-rpi4-speed',
                                       '--output', '/tmp/profile-build', '--capture', '/tmp/capture']), 0)
            self.assertEqual(execute.call_args.args[1:],
                             ('prepare-eeprom', 'polis-rpi4-speed', Path('/tmp/profile-build')))
            self.assertEqual(execute.call_args.kwargs, dict(capture=Path('/tmp/capture')))

    def test_interactive_actions_select_only_profiles(self):
        for action in ('configure', 'build', 'image', 'snapshot', 'audit'):
            with self.subTest(action=action), patch.object(cli, 'REPO', self.repo), \
                    patch.object(cli, 'choose', return_value='polis-rpi4-base') as select, \
                    patch.object(cli.profile_workflow, 'execute') as execute:
                self.assertEqual(cli.main([action]), 0)
                select.assert_called_once_with(self.ctx.profiles, 'Profile')
                self.assertEqual(execute.call_args.args[1:3], (action, 'polis-rpi4-base'))

    def test_noninteractive_actions_never_choose_a_default(self):
        for action in ('build', 'image'):
            with patch.object(cli, 'REPO', self.repo), patch('sys.stdin.isatty', return_value=False), \
                    patch.object(cli.profile_workflow, 'execute') as execute:
                self.assertEqual(cli.main([action]), 0)
                execute.assert_not_called()

    def test_legacy_profile_names_never_fall_back_to_defconfigs(self):
        (self.repo / 'configs').mkdir()
        (self.repo / 'configs/polis_rpi4_defconfig').write_text('')
        with patch.object(cli, 'REPO', self.repo), patch.object(cli.profile_workflow, 'execute') as execute, \
                contextlib.redirect_stderr(StringIO()):
            self.assertEqual(cli.main(['image', '--profile', 'polis_rpi4']), 1)
            execute.assert_not_called()

    def test_cancelled_selection_does_not_build(self):
        with patch.object(cli, 'REPO', self.repo), patch.object(cli, 'choose', return_value=None), \
                patch.object(cli.profile_workflow, 'execute') as execute:
            self.assertEqual(cli.main(['image']), 0)
            execute.assert_not_called()

    def test_selection_has_no_default_even_for_one_choice(self):
        with patch('sys.stdin.isatty', return_value=True), patch('builtins.input', return_value=''):
            self.assertIsNone(choose(['only-profile'], 'Profile'))

    def test_image_discovery_requires_a_completed_matching_profile_audit(self):
        image = self.image(self.output())
        self.image(self.repo / 'output/polis_rpi4')
        self.image(self.repo / 'output/polis-rpi4-speed-no-audit')
        failed = self.output('polis-rpi4-base')
        self.image(failed)
        (failed / 'profile/audit.json').write_text(json.dumps(dict(profile='polis-rpi4-base', state='failed')))
        self.assertEqual(images.discover_images(self.ctx), [image])
        saved = snapshots.snapshot(self.ctx, image.parent.parent, 'complete')
        self.assertEqual(images.discover_images(self.ctx, image.parent.parent), sorted([image, saved / 'images/sdcard.img']))

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


    def test_snapshot_archives_cargo_sources_without_build_outputs(self):
        output = self.output()
        self.image(output)
        sources = (
            'build-support/static-core/static-core.mk',
            'build-support/static-core/build.toml',
            'package/control/Cargo.toml', 'package/control/Cargo.lock',
            'package/control/firmware/src/main.rs',
            'package/probe/app/Cargo.toml', 'package/probe/app/src/main.rs',
            'package/control/src/target/mod.rs',
        )
        generated = (
            'package/control/target/debug/daemon',
            'package/probe/app/target/release/probe',
            'src/__pycache__/cli.pyc',
        )
        for name in sources + generated:
            path = self.repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(name)
        saved = snapshots.snapshot(self.ctx, output, 'sources-only')
        with tarfile.open(saved / 'external-at-capture.tar.gz') as archive:
            names = set(archive.getnames())
        self.assertTrue(set(sources) <= names)
        self.assertTrue(set(generated).isdisjoint(names))
        self.assertNotIn('package/control/target', names)
        self.assertNotIn('package/probe/app/target', names)
        subprocess.run(['sha256sum', '-c', '--quiet', 'SHA256SUMS'], cwd=saved, check=True)


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


    def test_standalone_snapshot_rejects_source_output_option(self):
        with contextlib.redirect_stderr(StringIO()), self.assertRaises(SystemExit):
            cli.parser().parse_args(['snapshot', '--source-output', str(self.br / 'output')])



if __name__ == '__main__':
    unittest.main()
