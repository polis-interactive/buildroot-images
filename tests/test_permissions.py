"""Host permission policy and installer checks; no system files or devices touched."""
import contextlib
import fnmatch
import io
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from src.common import UserError
from src.setup import permissions


def apply_policy(properties):
    """Evaluate this policy's equality/glob rules against synthetic device events.

    Real udev syntax is checked separately. Fixtures put ATTRS on one USB parent;
    no sysfs, udev events or device nodes are created by these checks.
    """
    result = {}
    for line in permissions.RULE_SOURCE.read_text().splitlines():
        if not line.strip() or line.startswith('#'):
            continue
        tokens = []
        for token in line.split(', '):
            match = re.fullmatch(r'([^=!]+?)(==|!=|:=|=)"([^"]*)"', token)
            if match is None:
                raise AssertionError('Unsupported test rule: ' + token)
            tokens.append(match.groups())
        matches = True
        for key, operator, value in tokens:
            if operator not in ('==', '!='):
                continue
            matched = any(fnmatch.fnmatchcase(properties.get(key, ''), pattern)
                          for pattern in value.split('|'))
            matches &= matched if operator == '==' else not matched
        if not matches:
            continue
        for key, operator, value in tokens:
            if operator in ('=', ':='):
                if key == 'GOTO':
                    return result
                if key != 'LABEL':
                    result[key] = value
    return result


class PermissionPolicyTests(unittest.TestCase):
    def disk(self, **overrides):
        return {'ACTION': 'add', 'SUBSYSTEM': 'block', 'ENV{DEVTYPE}': 'disk',
                'ENV{ID_BUS}': 'usb', 'ENV{ID_VENDOR_ID}': '0a5c',
                'ENV{ID_MODEL_ID}': '0104', 'ENV{ID_VENDOR}': 'mmcblk0',
                'ENV{ID_TYPE}': 'disk', 'ENV{ID_INSTANCE}': '0:0', **overrides}

    def test_sd_access_follows_device_type_across_ports_and_serials(self):
        for port, serial in (('1-2.3', 'first-pi'), ('7-9', 'second-pi')):
            result = apply_policy(self.disk(**{'ENV{ID_PATH}': port, 'ENV{ID_SERIAL_SHORT}': serial}))
            self.assertEqual(result, {'OWNER': 'root', 'GROUP': 'polis-wired', 'MODE': '0660',
                                      'ENV{UDISKS_IGNORE}': '1'})

    def test_other_disks_or_luns_receive_no_access_or_automount_override(self):
        for key, value in (('ENV{ID_BUS}', 'ata'), ('ENV{ID_VENDOR_ID}', '1234'),
                           ('ENV{ID_MODEL_ID}', '9999'), ('ENV{ID_VENDOR}', 'nvme0n1'),
                           ('ENV{ID_TYPE}', 'cd'), ('ENV{ID_INSTANCE}', '0:1')):
            with self.subTest(key=key):
                self.assertEqual(apply_policy(self.disk(**{key: value})), {})
                missing = self.disk(); missing.pop(key)
                self.assertEqual(apply_policy(missing), {})

    def test_partition_only_disables_automount_and_remove_events_are_ignored(self):
        self.assertEqual(apply_policy(self.disk(**{'ENV{DEVTYPE}': 'partition'})),
                         {'ENV{UDISKS_IGNORE}': '1'})
        self.assertEqual(apply_policy(self.disk(ACTION='remove')), {})

    def test_rom_access_is_limited_to_supported_usb_identity(self):
        rom = {'ACTION': 'add', 'SUBSYSTEM': 'usb', 'ENV{DEVTYPE}': 'usb_device',
               'ATTR{idVendor}': '0a5c', 'ATTR{idProduct}': '2711', 'KERNEL': '7-9'}
        self.assertEqual(apply_policy(rom)['GROUP'], 'polis-wired')
        self.assertEqual(apply_policy({**rom, 'ATTR{idProduct}': '2712'}), {})
        self.assertEqual(apply_policy({**rom, 'ENV{DEVTYPE}': 'usb_interface'}), {})

    def test_serial_access_requires_the_polis_device_identity(self):
        pico = {'ACTION': 'change', 'SUBSYSTEM': 'tty', 'KERNEL': 'ttyACM12',
                'ATTRS{idVendor}': 'c0de', 'ATTRS{idProduct}': 'cafe',
                'ATTRS{product}': 'Polis Wired Control'}
        self.assertEqual(apply_policy(pico)['MODE'], '0660')
        self.assertEqual(apply_policy({**pico, 'ATTRS{product}': 'Other device'}), {})
        pi = {'ACTION': 'add', 'SUBSYSTEM': 'tty', 'KERNEL': 'ttyACM3',
              'ATTRS{product}': 'Polis_Pi_Diagnostics'}
        self.assertEqual(apply_policy(pi)['GROUP'], 'polis-wired')
        self.assertEqual(apply_policy({**pi, 'KERNEL': 'ttyS0'}), {})

    @unittest.skipUnless(shutil.which('udevadm'), 'requires udevadm')
    def test_real_udev_parser_accepts_the_rules(self):
        version = int(subprocess.check_output(['udevadm', '--version'], text=True).split()[0])
        if version < 254:
            self.skipTest('udevadm verify requires systemd 254')
        permissions.validate(permissions.RULE_SOURCE)


class PermissionInstallTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.data = permissions.RULE_SOURCE.read_bytes()
        log = contextlib.redirect_stdout(io.StringIO())
        log.__enter__(); self.addCleanup(log.__exit__, None, None, None)

    def test_migration_preserves_old_rules_and_repeated_install_is_stable(self):
        for name in permissions.REPLACED_RULES:
            (self.root / name).write_text('old rule: ' + name)
        current = self.root / permissions.RULE_NAME
        current.write_text('previous managed policy')
        permissions.write_rules(self.root, self.data)
        self.assertEqual(current.read_bytes(), self.data)
        self.assertEqual(current.stat().st_mode & 0o777, 0o644)
        backups = {p.name: p.read_bytes() for p in self.root.glob('*.disabled')}
        self.assertEqual(len(backups), 4)
        self.assertEqual(list(self.root.glob('*.rules')), [current])
        permissions.write_rules(self.root, self.data)
        self.assertEqual({p.name: p.read_bytes() for p in self.root.glob('*.disabled')}, backups)

    def test_symlink_managed_rules_are_not_overwritten(self):
        original = self.root / 'admin-policy'; original.write_text('admin policy')
        (self.root / permissions.REPLACED_RULES[0]).symlink_to(original)
        with self.assertRaisesRegex(UserError, 'symlink'):
            permissions.write_rules(self.root, self.data)
        self.assertFalse((self.root / permissions.RULE_NAME).exists())
        self.assertEqual(original.read_text(), 'admin policy')

    def test_failed_publication_keeps_previous_rules_active(self):
        current = self.root / permissions.RULE_NAME
        legacy = self.root / permissions.REPLACED_RULES[0]
        current.write_text('previous policy')
        legacy.write_text('legacy policy')
        with patch.object(Path, 'replace', side_effect=OSError('cannot publish')):
            with self.assertRaisesRegex(OSError, 'cannot publish'):
                permissions.write_rules(self.root, self.data)
        self.assertEqual(current.read_text(), 'previous policy')
        self.assertEqual(legacy.read_text(), 'legacy policy')
        self.assertEqual(list(self.root.glob('.polis-rules-*')), [])

    def test_installer_enrolls_user_without_changing_other_groups_or_triggering_devices(self):
        account = SimpleNamespace(pw_uid=1000, pw_gid=1000)
        group = SimpleNamespace(gr_gid=555, gr_mem=[])
        with patch.object(permissions.os, 'geteuid', return_value=0), \
                patch.object(permissions.pwd, 'getpwnam', return_value=account), \
                patch.object(permissions.grp, 'getgrnam', side_effect=[KeyError(), group]), \
                patch.object(permissions.shutil, 'which', return_value='/usr/bin/tool'), \
                patch.object(permissions, 'RULES_DIRECTORY', self.root), \
                patch.object(permissions, 'run') as run:
            permissions.install('operator')
        commands = [call.args[0] for call in run.call_args_list]
        self.assertIn(['groupadd', '--system', 'polis-wired'], commands)
        self.assertIn(['usermod', '--append', '--groups', 'polis-wired', '--', 'operator'], commands)
        self.assertEqual(commands[-1], ['udevadm', 'control', '--reload-rules'])
        self.assertFalse(any('trigger' in command for command in commands))
        self.assertEqual((self.root / permissions.RULE_NAME).read_bytes(), self.data)

    def test_non_root_install_fails_before_any_changes(self):
        with patch.object(permissions.os, 'geteuid', return_value=1000), \
                patch.object(permissions, 'run') as run:
            with self.assertRaisesRegex(UserError, 'requires root'):
                permissions.install('operator')
        run.assert_not_called()
