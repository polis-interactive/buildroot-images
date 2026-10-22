"""First-boot safety boundaries; fake all device access and reboot operations."""
import importlib.util
from contextlib import nullcontext
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('firstboot', REPO / 'package/polis-firstboot/firstboot.py')
fb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fb)
spec = importlib.util.spec_from_file_location('firstboot_install', REPO / 'package/polis-firstboot/install.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class FakeTarget:
    def __init__(self):
        self.boot_id = 'boot-1'
        self.activity = False
        self.before = '[all]\nBOOT_ORDER=0xf41\nBOOT_UART=1\n'
        self.calls = []
        self.files = {}
        self.fail_stage = False
        self.unsupported = False

    def identity(self):
        return '12345678'

    def expand(self, directory):
        raise AssertionError('Boot-time storage expansion is forbidden')

    def config(self):
        self.calls.append('config')
        return self.before

    def version(self):
        return fb.FIRMWARE_VERSION

    def prepare(self, directory, before, after, version):
        self.calls.append('prepare')
        if self.unsupported:
            raise RuntimeError('unsupported firmware')
        (directory / 'after.conf').write_text(after)
        self.after = after
        return {'pieeprom.upd': 'payload', 'pieeprom.sig': 'signature'}

    def stage(self, directory, hashes):
        state = json.loads((directory / 'state.json').read_text())
        fb.require(state['phase'] == 'attempted' and state['boot_id'] == self.boot_id,
                   'Attempt must be durable before staging')
        self.calls.append('stage')
        self.files['pieeprom.upd'] = hashes['pieeprom.upd']
        if self.fail_stage:
            raise OSError('simulated power loss before signature')
        self.files.update(hashes)

    def cleanup(self, hashes):
        self.calls.append('cleanup')
        self.files.clear()

    def publish(self, report):
        self.report = report.copy()

    def reboot(self):
        self.calls.append('reboot')

    def next_boot(self):
        self.boot_id += '-next'
        self.activity = False
        self.calls.clear()


class FirstbootTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.target = FakeTarget()
        self.config = dict(enabled=True, settings={'NET_INSTALL_ENABLED': '0'})

    def run_setup(self):
        return fb.provision(self.target, self.config, self.root)

    def test_development_does_not_touch_storage_or_eeprom(self):
        self.config['enabled'] = False
        result = self.run_setup()
        self.assertEqual(result['status'], 'disabled')
        self.assertTrue(result['benchmark_eligible'])
        self.assertEqual(self.target.calls, [])
        self.assertEqual(list(self.root.iterdir()), [])

    def test_reboot_releases_busybox_sysinit_without_starting_services(self):
        target = object.__new__(fb.Target)
        with patch.object(fb, 'command') as command, patch.object(fb.os, 'sync'), \
                self.assertRaises(SystemExit) as raised:
            target.reboot()
        self.assertEqual(raised.exception.code, 75)
        command.assert_called_once_with('reboot')

    def test_update_once_verify_next_boot_and_steady_boot_is_eligible(self):
        first = self.run_setup()
        self.assertEqual(first['status'], 'reboot-required')
        self.assertFalse(first['benchmark_eligible'])
        self.assertEqual(self.target.calls, ['config', 'prepare', 'stage', 'reboot'])
        self.target.before = self.target.after
        self.target.next_boot()
        second = self.run_setup()
        self.assertEqual(second['eeprom'], 'verified')
        self.assertFalse(second['benchmark_eligible'])
        self.assertEqual(self.target.calls, ['cleanup', 'config'])
        self.assertEqual(self.target.files, {})
        self.target.next_boot()
        third = self.run_setup()
        self.assertTrue(third['benchmark_eligible'])
        self.assertEqual(self.target.calls, ['config'])

    def test_completed_record_does_not_hide_later_eeprom_changes(self):
        self.run_setup()
        self.target.before = self.target.after
        self.target.next_boot()
        self.run_setup()
        self.target.before = '[all]\nNET_INSTALL_ENABLED=1\n'
        self.target.next_boot()
        self.assertEqual(self.run_setup()['status'], 'failed')
        self.assertNotIn('stage', self.target.calls)

    def test_failed_verification_never_restages_or_reboots(self):
        self.run_setup()
        self.target.next_boot()
        self.assertEqual(self.run_setup()['status'], 'failed')
        self.assertIn('cleanup', self.target.calls)
        self.target.next_boot()
        self.assertEqual(self.run_setup()['status'], 'failed')
        self.assertEqual(self.target.calls, [])

    def test_interrupted_staging_cleans_partial_files_without_retry(self):
        self.target.fail_stage = True
        self.assertEqual(self.run_setup()['status'], 'failed')
        self.assertNotIn('reboot', self.target.calls)
        self.target.next_boot()
        self.assertEqual(self.run_setup()['status'], 'failed')
        self.assertEqual(self.target.files, {})
        self.assertNotIn('stage', self.target.calls)
        self.assertNotIn('reboot', self.target.calls)

    def test_same_boot_reentry_cannot_reboot_again(self):
        self.run_setup()
        self.target.calls.clear()
        self.assertEqual(self.run_setup()['status'], 'failed')
        self.assertEqual(self.target.calls, [])

    def test_no_boot_time_expansion_with_missing_or_matching_eeprom_settings(self):
        for settings in ({}, {'BOOT_UART': '1'}):
            with self.subTest(settings=settings):
                self.config['settings'] = settings
                self.target.next_boot()
                result = self.run_setup()
                self.assertEqual(result['status'], 'complete')
                self.assertNotIn('data', result)
                self.assertEqual(self.target.calls, ['config'] if settings else [])
                self.assertFalse((self.root / 'storage').exists())

    def test_legacy_storage_records_are_ignored_and_preserved(self):
        storage = self.root / 'storage'
        storage.mkdir()
        record = storage / 'result.json'
        record.write_text('{"status":"failed","bytes":1234}\n')
        original = record.read_bytes()
        self.config['settings'] = {}
        for _ in range(2):
            self.target.next_boot()
            result = self.run_setup()
            self.assertEqual(result['status'], 'complete')
            self.assertTrue(result['benchmark_eligible'])
            self.assertFalse(result['activity'])
            self.assertEqual(self.target.calls, [])
            self.assertNotIn('data', result)
            self.assertEqual(record.read_bytes(), original)

    def test_unsupported_firmware_does_not_keep_preparing(self):
        self.target.unsupported = True
        self.assertEqual(self.run_setup()['status'], 'failed')
        self.target.next_boot()
        self.assertEqual(self.run_setup()['status'], 'failed')
        self.assertEqual(self.target.calls, [])

    def test_merge_preserves_unrelated_settings_and_comments(self):
        before = '# board config\n[all]\nBOOT_ORDER=0xf41\nNET_INSTALL_ENABLED=1\n[none]\nFOO=bar\n'
        merged = fb.merge_config(before, {'NET_INSTALL_ENABLED': '0'})
        self.assertIn('# board config\n[all]\nBOOT_ORDER=0xf41\n[none]\nFOO=bar\n', merged)
        self.assertTrue(merged.endswith('[all]\nNET_INSTALL_ENABLED=0\n'))
        self.assertEqual(fb.merge_config(merged, {'NET_INSTALL_ENABLED': '0'}), merged)
        with self.assertRaisesRegex(RuntimeError, 'conditional'):
            fb.merge_config('[gpio8=1]\nNET_INSTALL_ENABLED=1\n', {'NET_INSTALL_ENABLED': '0'})
        with self.assertRaisesRegex(RuntimeError, 'Invalid'):
            fb.merge_config(before, {'NET_INSTALL_ENABLED': '0\nBOOT_ORDER=1'})

    def test_installer_development_removes_payload_and_uses_no_python(self):
        target = self.root / 'target'
        (target / 'etc/init.d').mkdir(parents=True)
        assets = target / 'usr/share/polis-firstboot'
        assets.mkdir(parents=True)
        for name in ('rpi-eeprom-config', 'pieeprom.bin'):
            (assets / name).write_text('fixture')
        plan = dict(development=False, eeprom={'NET_INSTALL_ENABLED': '0'}, fingerprint='abc')
        installer.install(target, plan)
        self.assertTrue((assets / 'firstboot.py').is_file())
        self.assertIn('python3', (target / 'etc/init.d/polis-firstboot').read_text())
        plan['development'] = True
        installer.install(target, plan)
        self.assertFalse(assets.exists())
        self.assertNotIn('python3', (target / 'etc/init.d/polis-firstboot').read_text())
        self.assertFalse(json.loads((target / 'etc/polis-firstboot.json').read_text())['enabled'])

    def test_fat_staging_commits_signature_last_and_cleanup_preserves_foreign_files(self):
        recovery, boot = self.root / 'recovery', self.root / 'boot'
        recovery.mkdir()
        boot.mkdir()
        hashes = {}
        for name in ('pieeprom.upd', 'pieeprom.sig'):
            (recovery / name).write_text(name)
            hashes[name] = fb.sha(name.encode())
        target = object.__new__(fb.Target)
        with patch.object(target, 'boot', side_effect=lambda: nullcontext(boot)), \
                patch.object(fb.os, 'sync'), patch.object(fb, 'atomic', wraps=fb.atomic) as atomic:
            target.stage(recovery, hashes)
            self.assertEqual([call.args[0].name for call in atomic.call_args_list],
                             ['pieeprom.upd', 'pieeprom.sig'])
            with self.assertRaisesRegex(RuntimeError, 'Foreign'):
                target.stage(recovery, hashes)
            (boot / 'pieeprom.upd').write_text('foreign update')
            with self.assertRaisesRegex(RuntimeError, 'Foreign'):
                target.cleanup(hashes)
            self.assertTrue((boot / 'pieeprom.sig').exists())
            (boot / 'pieeprom.upd').write_text('pieeprom.upd')
            target.cleanup(hashes)
            self.assertEqual(list(boot.iterdir()), [])

    def test_upstream_tool_roundtrip_and_rollback_timestamp(self):
        source = REPO / '.local/usbboot/rpi-eeprom'
        if not (source / 'rpi-eeprom-config').exists():
            self.skipTest('optional pinned upstream checkout unavailable')
        assets = self.root / 'assets'
        assets.mkdir()
        shutil.copy2(source / 'rpi-eeprom-config', assets)
        shutil.copy2(source / 'firmware-2711/old/stable/pieeprom-2025-11-05.bin', assets / 'pieeprom.bin')
        before = '[all]\nBOOT_UART=1\nBOOT_ORDER=0xf41\n'
        after = fb.merge_config(before, {'NET_INSTALL_ENABLED': '0'})
        target = object.__new__(fb.Target)
        directory = self.root / 'recovery'
        directory.mkdir()
        with patch.object(fb, 'ASSETS', assets), patch.object(fb.os, 'sync'):
            hashes = target.prepare(directory, before, after, fb.FIRMWARE_VERSION + '\nupdate-time 12345\n')
        for name, digest in hashes.items():
            self.assertEqual(fb.sha((directory / name).read_bytes()), digest)
        self.assertEqual((directory / 'before.conf').read_text(), before)
        update_ts = int((directory / 'pieeprom.sig').read_text().split('ts: ')[1].splitlines()[0])
        rollback_ts = int((directory / 'rollback.sig').read_text().split('ts: ')[1].splitlines()[0])
        self.assertEqual(rollback_ts, update_ts + 1)
        extracted = subprocess.check_output(['python3', str(assets / 'rpi-eeprom-config'), str(directory / 'rollback.bin')], text=True)
        self.assertEqual(fb.active(extracted), fb.active(before))


if __name__ == '__main__':
    unittest.main()
