"""Expansion regressions; real filesystem tests use only temporary regular files."""
import contextlib
import copy
import hashlib
from io import StringIO
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from src import cli
from src.artifacts import images
from src.common import Context, UserError, run


def table(path, data_size=32768):
    separator = 'p' if str(path)[-1].isdigit() else ''
    return dict(label='dos', id='0x12345678', device=str(path), unit='sectors', sectorsize=512,
                partitions=[dict(node=f'{path}{separator}{i}', start=start, size=size, type=kind)
                            for i, start, size, kind in ((1, 2048, 2048, 'c'),
                                                        (2, 4096, 2048, '83'),
                                                        (3, 6144, data_size, '83'))])


class ExpansionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.image = self.root / 'source image'
        self.image.touch()
        self.device = self.root / 'card'
        self.device.touch()
        self.image_size = 38912 * 512
        self.disk_size = 64 << 20
        self.identity = (123, 'serial', self.disk_size)
        self.source = table(self.image)
        self.ctx = Context(self.root, self.root, self.root, self.root)
        self.enterContext(contextlib.redirect_stdout(StringIO()))

    def test_discovery_uses_the_shared_completed_output(self):
        output = self.root / '.output'
        (output / 'profile').mkdir(parents=True)
        (output / 'images').mkdir()
        image = output / 'images/sdcard.img'
        image.write_bytes(b'image')
        audit = output / 'profile/audit.json'
        with patch.object(images.snapshots, 'completed', return_value=[]):
            audit.write_text(json.dumps({'profile': 'screen', 'state': 'built'}))
            self.assertEqual(images.discover_images(self.ctx), [image])
            audit.write_text(json.dumps({'profile': 'screen', 'state': 'configured'}))
            self.assertEqual(images.discover_images(self.ctx), [])

    def test_flag_defaults_off_and_is_forwarded(self):
        self.assertFalse(cli.parser().parse_args(['flash']).expand_data)
        with patch.object(images, 'flash') as flash:
            self.assertEqual(cli.main(['flash', '--expand-data']), 0)
        self.assertTrue(flash.call_args.kwargs['expand_data'])

    def test_reject_unsupported_layouts(self):
        cases = []
        for field, value in (('label', 'gpt'), ('sectorsize', 4096), ('unit', 'bytes')):
            bad = copy.deepcopy(self.source)
            bad[field] = value
            cases.append(bad)
        bad = copy.deepcopy(self.source)
        bad['partitions'].pop()
        cases.append(bad)
        for field, value in (('start', 1), ('size', 0), ('size', 999999),
                             ('type', '5'), ('node', str(self.image) + '4')):
            bad = copy.deepcopy(self.source)
            bad['partitions'][2][field] = value
            cases.append(bad)
        for bad in cases:
            with self.subTest(table=bad), patch.object(images, 'run') as command:
                command.return_value.stdout = json.dumps({'partitiontable': bad})
                with self.assertRaises(UserError):
                    images.data_layout(self.image, self.image_size)

    def test_filesystem_requires_ext4_and_label(self):
        for tags in ('TYPE=ext4\nLABEL=other\n', 'TYPE=ext3\nLABEL=polis-data\n'):
            with self.subTest(tags=tags), patch.object(images, 'run') as command:
                command.return_value.stdout = tags
                with self.assertRaisesRegex(UserError, 'polis-data'):
                    images.check_data_filesystem(self.image, self.source['partitions'][2])

    def test_mmc_partition_names(self):
        device = Path('/dev/mmcblk0')
        with patch.object(images, 'run') as command:
            command.return_value.stdout = json.dumps({'partitiontable': table(device)})
            layout = images.data_layout(device, self.disk_size)
        self.assertEqual(layout['partitions'][2]['node'], '/dev/mmcblk0p3')

    def flash_mocks(self):
        self.image.write_bytes(b'image')
        self.enterContext(patch('sys.stdin.isatty', return_value=True))
        self.enterContext(patch('os.geteuid', return_value=0))
        self.enterContext(patch('builtins.input', return_value=str(self.device)))
        self.enterContext(patch.object(images.shutil, 'which', return_value='/host/tool'))
        self.enterContext(patch.object(images, 'disks', return_value=[]))
        self.enterContext(patch.object(images, 'inspect_destination', return_value=self.identity))
        self.enterContext(patch.object(images, 'data_layout', return_value=self.source))
        self.enterContext(patch.object(images, 'check_data_filesystem'))
        command = self.enterContext(patch.object(images, 'run'))
        command.return_value.stdout = '512\n'
        return command

    def test_preflight_failures_never_flash(self):
        command = self.flash_mocks()
        for target, kwargs in (
                ('shutil.which', dict(return_value=None)),
                ('data_layout', dict(side_effect=UserError('unsupported layout'))),
                ('check_data_filesystem', dict(side_effect=UserError('wrong filesystem')))):
            with self.subTest(target=target), patch('src.artifacts.images.' + target, **kwargs):
                with self.assertRaises(UserError):
                    images.flash(self.ctx, self.image, self.device, expand_data=True)
                command.assert_not_called()

    def test_4k_sector_destination_never_flashes(self):
        command = self.flash_mocks()
        command.return_value.stdout = '4096\n'
        with self.assertRaisesRegex(UserError, '512-byte logical sectors'):
            images.flash(self.ctx, self.image, self.device, expand_data=True)
        self.assertEqual([call.args[0][0] for call in command.call_args_list], ['blockdev'])

    def test_cancel_with_expansion_never_writes(self):
        command = self.flash_mocks()
        with patch('builtins.input', return_value='no'):
            with self.assertRaisesRegex(UserError, 'Cancelled'):
                images.flash(self.ctx, self.image, self.device, expand_data=True)
        command.assert_not_called()

    def test_probe_atime_change_allows_flash_then_expansion(self):
        command = self.flash_mocks()
        os.utime(self.image, (1, 1))
        with patch.object(images, 'check_data_filesystem', side_effect=lambda *args: self.image.read_bytes()), \
                patch.object(images, 'grow_data') as grow:
            images.flash(self.ctx, self.image, self.device, expand_data=True)
        self.assertEqual(command.call_args.args[0][0], 'dd')
        grow.assert_called_once_with(self.device, self.identity, self.image.stat().st_size, self.source, [])

    def test_changed_source_never_flashes(self):
        command = self.flash_mocks()

        def confirm(*args):
            self.image.write_bytes(b'changed image')
            return str(self.device)

        with patch('builtins.input', side_effect=confirm):
            with self.assertRaisesRegex(UserError, 'Image changed'):
                images.flash(self.ctx, self.image, self.device, expand_data=True)
        command.assert_not_called()

    def test_flash_precedes_expansion_and_reports_partial_failure(self):
        command = self.flash_mocks()

        def fail(*args):
            self.assertEqual(command.call_args.args[0][0], 'dd')
            raise UserError('filesystem check failed')

        with patch.object(images, 'grow_data', side_effect=fail):
            with self.assertRaisesRegex(UserError, 'Image was written, but /data expansion failed'):
                images.flash(self.ctx, self.image, self.device, expand_data=True)

    def test_hostname_is_written_after_image_and_before_expansion(self):
        command = self.flash_mocks()
        prepared = self.root / 'prepared.ext4'
        def after_hostname(*args):
            self.assertIn(f'if={prepared}', command.call_args.args[0])
            self.assertIn('conv=notrunc,fsync', command.call_args.args[0])
        with patch.object(images.hostname_config, 'prepare', return_value=prepared), \
                patch.object(images, 'grow_data', side_effect=after_hostname) as grow:
            images.flash(self.ctx, self.image, self.device, hostname='polis-2', expand_data=True)
        writes = [call.args[0] for call in command.call_args_list if call.args[0][0] == 'dd']
        self.assertEqual(len(writes), 2)
        self.assertIn(f'if={self.image}', writes[0])
        self.assertIn(f'if={prepared}', writes[1])
        self.assertIn('seek=6144', writes[1])
        self.assertIn('conv=notrunc,fsync', writes[1])
        grow.assert_called_once()

    def test_hostname_compatibility_failure_never_writes(self):
        command = self.flash_mocks()
        with patch.object(images.hostname_config, 'prepare', side_effect=UserError('old image')):
            with self.assertRaisesRegex(UserError, 'old image'):
                images.flash(self.ctx, self.image, self.device, hostname='polis-2')
        command.assert_not_called()

    def test_cancel_with_hostname_never_writes(self):
        command = self.flash_mocks()
        with patch.object(images.hostname_config, 'prepare', return_value=self.root / 'data.ext4'), \
                patch('builtins.input', return_value='no'):
            with self.assertRaisesRegex(UserError, 'Cancelled'):
                images.flash(self.ctx, self.image, self.device, hostname='polis-2')
        command.assert_not_called()

    def test_hostname_write_failure_reports_partial_flash(self):
        command = self.flash_mocks()
        def write(args, **kwargs):
            if 'conv=notrunc,fsync' in args:
                raise OSError('write failed')
            return subprocess.CompletedProcess(args, 0, '512\n', '')
        command.side_effect = write
        with patch.object(images.hostname_config, 'prepare', return_value=self.root / 'data.ext4'):
            with self.assertRaisesRegex(UserError, 'Image was written, but per-card configuration failed'):
                images.flash(self.ctx, self.image, self.device, hostname='polis-2')

    def test_ap_only_prepares_and_writes_data(self):
        command = self.flash_mocks()
        prepared = self.root / 'prepared.ext4'
        with patch.object(images.hostname_config, 'prepare', return_value=prepared) as prepare:
            images.flash(self.ctx, self.image, self.device, ap_name='Polis-Setup-2')
        self.assertIsNone(prepare.call_args.args[3])
        self.assertEqual(prepare.call_args.kwargs, dict(ap_name='Polis-Setup-2'))
        self.assertIn(f'if={prepared}', command.call_args.args[0])
        self.assertIn('conv=notrunc,fsync', command.call_args.args[0])

    def test_cancel_with_ap_name_never_writes(self):
        command = self.flash_mocks()
        with patch.object(images.hostname_config, 'prepare', return_value=self.root / 'data.ext4'), \
                patch('builtins.input', return_value='no'):
            with self.assertRaisesRegex(UserError, 'Cancelled'):
                images.flash(self.ctx, self.image, self.device, ap_name='Polis-Setup-2')
        command.assert_not_called()

    def test_ap_preflight_failure_never_writes(self):
        command = self.flash_mocks()
        with patch.object(images.hostname_config, 'prepare', side_effect=UserError('missing Wi-Fi')):
            with self.assertRaisesRegex(UserError, 'missing Wi-Fi'):
                images.flash(self.ctx, self.image, self.device, ap_name='Polis-Setup-2')
        command.assert_not_called()

    def grow_mocks(self, fsck_status=0, kernel_size=None, fs_size=None, changed_layout=False,
                   block_size=4096):
        start = self.source['partitions'][2]['start']
        sectors = self.disk_size // 512 - start
        before = table(self.device)
        after = table(self.device, sectors)
        before['partitions'][2]['start'] = after['partitions'][2]['start'] = start
        if changed_layout:
            after['partitions'][1]['start'] += 1
        self.enterContext(patch.object(images, 'data_layout', side_effect=[before, after]))
        self.enterContext(patch.object(images, 'check_data_filesystem'))
        inspect = self.enterContext(patch.object(images, 'inspect_destination', return_value=self.identity))

        def command(args, **kwargs):
            if args[0] == 'e2fsck' and fsck_status:
                raise subprocess.CalledProcessError(fsck_status, args)
            stdout = ''
            if '--getsize64' in args:
                stdout = str(kernel_size if kernel_size is not None else sectors * 512)
            if 'dumpe2fs' in args:
                size = fs_size if fs_size is not None else sectors * 512
                stdout = f'Block count: {size // block_size}\nBlock size: {block_size}\n'
            return subprocess.CompletedProcess(args, 0, stdout, '')

        calls = self.enterContext(patch.object(images, 'run', side_effect=command))
        return calls, inspect

    def test_corrected_fsck_is_success_and_only_data_size_changes(self):
        calls, _ = self.grow_mocks(fsck_status=1)
        images.grow_data(self.device, self.identity, self.image_size, self.source, [])
        writes = [call for call in calls.call_args_list if call.args[0][0] == 'sfdisk']
        self.assertEqual(len(writes), 1)
        self.assertIn('3', writes[0].args[0])
        self.assertEqual(writes[0].kwargs['input'], f'size={self.disk_size // 512 - 6144}\n')
        self.assertEqual(calls.call_args.args[0], ['blockdev', '--flushbufs', self.device])

    def test_fatal_fsck_stops_before_resize(self):
        calls, _ = self.grow_mocks(fsck_status=4)
        with self.assertRaises(subprocess.CalledProcessError):
            images.grow_data(self.device, self.identity, self.image_size, self.source, [])
        self.assertNotIn('resize2fs', [call.args[0][0] for call in calls.call_args_list])

    def test_already_full_partition_skips_table_write(self):
        self.disk_size = self.image_size
        self.identity = (123, 'serial', self.disk_size)
        calls, _ = self.grow_mocks()
        images.grow_data(self.device, self.identity, self.image_size, self.source, [])
        self.assertNotIn('sfdisk', [call.args[0][0] for call in calls.call_args_list])

    def test_mount_after_partition_reread_stops_before_mutation(self):
        calls, inspect = self.grow_mocks()
        inspect.side_effect = [self.identity, UserError('mounted filesystem')]
        with self.assertRaisesRegex(UserError, 'mounted'):
            images.grow_data(self.device, self.identity, self.image_size, self.source, [])
        self.assertNotIn('sfdisk', [call.args[0][0] for call in calls.call_args_list])

    def test_wrong_kernel_size_stops_before_fsck(self):
        calls, _ = self.grow_mocks(kernel_size=1234)
        with self.assertRaisesRegex(UserError, 'Kernel has not accepted'):
            images.grow_data(self.device, self.identity, self.image_size, self.source, [])
        self.assertNotIn('e2fsck', [call.args[0][0] for call in calls.call_args_list])

    def test_incomplete_filesystem_growth_is_reported(self):
        self.grow_mocks(fs_size=16 << 20)
        with self.assertRaisesRegex(UserError, 'did not grow'):
            images.grow_data(self.device, self.identity, self.image_size, self.source, [])

    def test_reported_card_size_accepts_page_rounded_1k_filesystem(self):
        self.disk_size = 15931539456
        self.identity = (123, 'serial', self.disk_size)
        self.source['partitions'][2]['start'] = 507905
        calls, _ = self.grow_mocks(fs_size=15304188 * 1024, block_size=1024)
        with patch.object(images.os, 'sysconf', return_value=4096):
            images.grow_data(self.device, self.identity, self.image_size, self.source, [])
        self.assertEqual(calls.call_args.args[0], ['blockdev', '--flushbufs', self.device])

    def test_page_rounding_uses_host_page_size(self):
        self.disk_size -= 512
        self.identity = (123, 'serial', self.disk_size)
        size = (self.disk_size - 6144 * 512) // 65536 * 65536
        self.grow_mocks(fs_size=size, block_size=1024)
        with patch.object(images.os, 'sysconf', return_value=65536):
            images.grow_data(self.device, self.identity, self.image_size, self.source, [])

    def test_full_page_of_missing_growth_is_rejected(self):
        size = self.disk_size - 6144 * 512 - 4096
        self.grow_mocks(fs_size=size, block_size=1024)
        with patch.object(images.os, 'sysconf', return_value=4096):
            with self.assertRaisesRegex(UserError, 'did not grow'):
                images.grow_data(self.device, self.identity, self.image_size, self.source, [])

    def test_filesystem_larger_than_partition_is_rejected(self):
        self.grow_mocks(fs_size=self.disk_size - 6144 * 512 + 4096)
        with self.assertRaisesRegex(UserError, 'did not grow'):
            images.grow_data(self.device, self.identity, self.image_size, self.source, [])

    def test_unexpected_table_change_stops_before_fsck(self):
        calls, _ = self.grow_mocks(changed_layout=True)
        with self.assertRaisesRegex(UserError, 'Unexpected partition table'):
            images.grow_data(self.device, self.identity, self.image_size, self.source, [])
        self.assertNotIn('e2fsck', [call.args[0][0] for call in calls.call_args_list])


@unittest.skipUnless(all(shutil.which(tool) for tool in
                        ('sfdisk', 'blkid', 'mkfs.ext4', 'e2fsck', 'resize2fs', 'dumpe2fs', 'debugfs')),
                     'requires util-linux and e2fsprogs host tools')
class ExpansionFilesTest(unittest.TestCase):
    def test_real_partition_and_filesystem_growth_preserves_source_and_contents(self):
        self.check_real_growth(64 << 20)

    def test_real_page_rounded_filesystem_growth_preserves_contents(self):
        self.check_real_growth((64 << 20) - 512)

    def check_real_growth(self, disk_size):
        with tempfile.TemporaryDirectory(prefix='polis-expand-') as tmp:
            root = Path(tmp)
            source, card, data = [root / name for name in ('source.img', 'card.img', 'data.ext4')]
            seed = root / 'seed'
            seed.mkdir()
            (seed / 'marker').write_text('keep this application state\n')
            with data.open('wb') as stream:
                stream.truncate(16 << 20)
            run(['mkfs.ext4', '-q', '-F', '-b', '1024', '-L', 'polis-data', '-d', seed, data], capture=True)
            with source.open('wb') as stream:
                stream.truncate(38912 * 512)
            run(['sfdisk', source], input='label: dos\nlabel-id: 0x12345678\n'
                'start=2048,size=2048,type=c,bootable\nstart=4096,size=2048,type=83\n'
                'start=6144,size=32768,type=83\n', capture=True)
            with source.open('r+b') as stream:
                stream.seek(2048 * 512)
                stream.write(b'boot payload')
                stream.seek(4096 * 512)
                stream.write(b'root payload')
                stream.seek(6144 * 512)
                stream.write(data.read_bytes())
            original_hash = hashlib.sha256(source.read_bytes()).digest()
            layout = images.data_layout(source, source.stat().st_size)
            images.check_data_filesystem(source, layout['partitions'][2])
            shutil.copyfile(source, card)
            with card.open('r+b') as stream:
                stream.truncate(disk_size)
            identity = (123, 'fixture', card.stat().st_size)

            def host_command(args, **kwargs):
                # Emulate kernel partition nodes with a regular file. All actual
                # sfdisk/e2fsck/resize2fs operations still use the production code.
                if args[0] in ('blockdev', 'udevadm'):
                    if '--getsize64' in args:
                        part = images.data_layout(card, identity[2])['partitions'][2]
                        with data.open('r+b') as stream:
                            stream.truncate(part['size'] * 512)
                        return subprocess.CompletedProcess(args, 0, str(part['size'] * 512), '')
                    return subprocess.CompletedProcess(args, 0, '', '')
                args = [data if str(arg) == str(card) + '3' else arg for arg in args]
                kwargs['capture'] = True
                return run(args, **kwargs)

            with patch.object(images, 'inspect_destination', return_value=identity), \
                    patch.object(images, 'run', side_effect=host_command), \
                    contextlib.redirect_stdout(StringIO()):
                images.grow_data(card, identity, source.stat().st_size, layout, [])
            run(['e2fsck', '-f', '-n', data], capture=True)
            marker = run(['debugfs', '-R', 'cat /marker', data], capture=True).stdout
            self.assertEqual(marker, 'keep this application state\n')
            after = images.data_layout(card, identity[2])
            self.assertEqual(after['partitions'][2]['start'] + after['partitions'][2]['size'], identity[2] // 512)
            with source.open('rb') as old, card.open('rb') as new:
                old.seek(512)
                new.seek(512)
                self.assertEqual(old.read(6144 * 512 - 512), new.read(6144 * 512 - 512))
            self.assertEqual(hashlib.sha256(source.read_bytes()).digest(), original_hash)


if __name__ == '__main__':
    unittest.main()
