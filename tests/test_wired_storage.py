"""Simulated fixture media tests. Never opens a real block device."""
import hashlib
import io
import os
import fcntl
import tarfile
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
from src import wired_storage as storage
from src.common import UserError
from src.images import validate_disk


class WiredStorageTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.image = self.root / 'image.img'
        self.data = bytes(range(256)) * 8
        self.image.write_bytes(self.data)
        self.spec = {'path': str(self.image), 'sha256': hashlib.sha256(self.data).hexdigest()}
        self.config = {'fixture': 'bench', 'usb_path': '1-2.3', 'disk_id_path': 'pci-usb-lun0', 'disk_serial': 'pi-serial'}

    def tearDown(self):
        self.temp.cleanup()

    def test_byte_copy_and_readback_verification(self):
        target = self.root / 'simulated-target'
        with self.image.open('rb') as source, target.open('w+b') as dest:
            storage.copy_verify(source, dest, len(self.data), self.spec['sha256'])
        self.assertEqual(target.read_bytes(), self.data)

    def test_failed_verification_is_not_success(self):
        with self.image.open('rb') as source, (self.root/'target').open('w+b') as dest:
            with self.assertRaisesRegex(UserError, 'mismatch'):
                storage.copy_verify(source, dest, len(self.data), '0' * 64)

    def test_direct_readback_and_flags_restored(self):
        # Real direct I/O against a disposable regular file, never physical media.
        data = b'a' * storage.CHUNK + b'b' * 512
        self.image.write_bytes(data)
        with self.image.open('rb', buffering=0) as stream:
            flags = fcntl.fcntl(stream.fileno(), fcntl.F_GETFL)
            self.assertEqual(storage.direct_digest(stream.fileno(), len(data)),
                             hashlib.sha256(data).hexdigest())
            self.assertEqual(fcntl.fcntl(stream.fileno(), fcntl.F_GETFL), flags)

    def test_direct_read_errors_never_fall_back_to_buffered_verification(self):
        with self.image.open('rb', buffering=0) as stream:
            flags = fcntl.fcntl(stream.fileno(), fcntl.F_GETFL)
            with patch.object(os, 'readv', side_effect=OSError('direct read failed')):
                with self.assertRaisesRegex(OSError, 'direct read failed'):
                    storage.direct_digest(stream.fileno(), 512)
            with patch.object(os, 'readv', return_value=0):
                with self.assertRaisesRegex(UserError, 'Short direct read'):
                    storage.direct_digest(stream.fileno(), 512)
            self.assertEqual(fcntl.fcntl(stream.fileno(), fcntl.F_GETFL), flags)

    def test_unsupported_direct_io_stops_before_any_write(self):
        with self.image.open('rb') as source, (self.root/'target').open('w+b') as dest:
            with patch.object(storage.stat, 'S_ISBLK', return_value=True), \
                    patch.object(storage.fcntl, 'ioctl', return_value=(512).to_bytes(4, 'little')), \
                    patch.object(storage, 'direct_digest', side_effect=OSError('unsupported')):
                with self.assertRaisesRegex(OSError, 'unsupported'):
                    storage.copy_verify(source, dest, len(self.data), self.spec['sha256'])
            self.assertEqual(dest.tell(), 0)
            self.assertEqual(os.fstat(dest.fileno()).st_size, 0)

    def test_bad_image_hash_and_incomplete_snapshot_rejected(self):
        with self.assertRaisesRegex(UserError, 'mismatch'):
            storage.image_open({**self.spec, 'sha256': '0' * 64})
        snapshot = self.root/'snapshot'; (snapshot/'images').mkdir(parents=True)
        img = snapshot/'images/sdcard.img'; img.write_bytes(self.data)
        (snapshot/'.incomplete').touch()
        with self.assertRaisesRegex(UserError, 'Incomplete'):
            storage.image_open({**self.spec, 'path': str(img)})

    def test_unconfirmed_flash_never_opens_destination(self):
        with patch.object(storage, 'checked_disk') as check:
            with self.assertRaisesRegex(UserError, 'confirmation'):
                storage.flash(self.config, self.spec, 'other-fixture')
            check.assert_not_called()

    def test_disk_match_requires_topology_and_both_explicit_identities(self):
        device = self.root/'sdx'; device.touch()
        props = {'ID_BUS': 'usb', 'ID_PATH': 'pci-usb-lun0', 'ID_SERIAL_SHORT': 'pi-serial'}
        with patch.object(storage, 'disks', return_value=[{'type': 'disk', 'name': str(device)}]), \
                patch.object(storage, 'usb_ancestry', return_value=['1-2.3']), \
                patch.object(storage, 'properties', return_value=props):
            self.assertEqual(storage.matching_disk(self.config), device)
            self.assertIsNone(storage.matching_disk({**self.config, 'usb_path': '2-1'}))
            self.assertIsNone(storage.matching_disk({**self.config, 'disk_serial': 'other'}))
            self.assertIsNone(storage.matching_disk({**self.config, 'disk_id_path': 'other-lun'}))

    def test_duplicate_identity_never_selects_first_disk(self):
        devices = [self.root/'sda', self.root/'sdb']
        for device in devices: device.touch()
        with patch.object(storage, 'disks', return_value=[{'type': 'disk', 'name': str(d)} for d in devices]), \
                patch.object(storage, 'usb_ancestry', return_value=['1-2.3']), \
                patch.object(storage, 'properties', return_value={'ID_BUS': 'usb', 'ID_PATH': 'pci-usb-lun0', 'ID_SERIAL_SHORT': 'pi-serial'}):
            with self.assertRaisesRegex(UserError, 'Ambiguous'):
                storage.matching_disk(self.config)

    def test_virtual_or_disappearing_disk_is_not_a_usb_candidate(self):
        with patch.object(Path, 'resolve', side_effect=FileNotFoundError):
            self.assertEqual(storage.usb_ancestry(Path('/dev/simulated-zram')), [])

    def test_existing_validator_rejects_mounted_swap_readonly_and_small_media(self):
        valid = {'type': 'disk', 'size': 4096, 'ro': False, 'mountpoints': [], 'children': []}
        for bad in [dict(valid, ro=True), dict(valid, size=1), dict(valid, type='part'),
                    dict(valid, children=[{'mountpoints': ['/mnt/card']}]),
                    dict(valid, children=[{'mountpoints': ['[SWAP]']}])]:
            with self.assertRaises(UserError): validate_disk(bad, 2048)

    def test_payload_hash_pinning_and_provisioning_rejection(self):
        root = self.root/'gadget'; root.mkdir()
        (root/'boot.img').write_bytes(b'RAM environment')
        (root/'bootcode4.bin').write_bytes(b'loader')
        (root/'config.txt').write_text('boot_ramdisk=1\n')
        config = {'gadget_dir': str(root), 'gadget_sha256': storage.payload_hash(root)}
        storage.validate_payload(config)
        (root/'boot.img').write_bytes(b'changed')
        with self.assertRaisesRegex(UserError, 'hash mismatch'): storage.validate_payload(config)
        (root/'recovery.bin').write_bytes(b'forbidden')
        config['gadget_sha256'] = storage.payload_hash(root)
        with self.assertRaisesRegex(UserError, 'forbidden'): storage.validate_payload(config)

    def test_missing_otp_prevents_preflight(self):
        with patch.object(storage, 'validate_payload'):
            with self.assertRaisesRegex(UserError, 'OTP'):
                storage.dispatch({'action': 'preflight', 'config': {'otp_gpio8_provisioned': False}})

    def test_official_bootfiles_archive_and_invalid_variants(self):
        root = self.root/'gadget'; root.mkdir()
        (root/'boot.img').write_bytes(b'RAM environment')
        (root/'config.txt').write_text('boot_ramdisk=1\n')
        def archive(names):
            with tarfile.open(root/'bootfiles.bin', 'w') as files:
                for name in names:
                    member = tarfile.TarInfo(name); member.size = 6
                    files.addfile(member, io.BytesIO(b'loader'))
        def validate():
            storage.validate_payload({'gadget_dir': str(root),
                                      'gadget_sha256': storage.payload_hash(root)})
        archive(['2711/bootcode4.bin'])
        validate()
        archive(['2712/bootcode5.bin'])
        with self.assertRaisesRegex(UserError, 'Pi 4 loader'): validate()
        archive(['2711/bootcode4.bin', '2711/recovery.bin'])
        with self.assertRaisesRegex(UserError, 'forbidden'): validate()
        archive(['2711/bootcode4.bin', '2711/bootcode4.bin'])
        with self.assertRaisesRegex(UserError, 'Pi 4 loader'): validate()
        (root/'bootfiles.bin').write_bytes(b'broken archive')
        with self.assertRaisesRegex(UserError, 'Invalid bootfiles'): validate()
