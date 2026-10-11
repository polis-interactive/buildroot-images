"""EEPROM preparation, automatic workflow ordering, and portable snapshot evidence."""
import contextlib
from dataclasses import replace
import io
import json
from pathlib import Path
import shutil
from unittest.mock import patch

import unittest
import test_profile_builds
from src.common import UserError
from src.profiles import provision, resolver, workflow
from src.artifacts import snapshots


class EepromTests(unittest.TestCase):
    setUp = test_profile_builds.ProfileBuildTests.setUp
    plan = test_profile_builds.ProfileBuildTests.plan
    configure = test_profile_builds.ProfileBuildTests.configure
    fake_configure = test_profile_builds.ProfileBuildTests.fake_configure
    def capture(self, configured=False):
        path = self.root / 'capture'
        path.mkdir(exist_ok=True)
        (path / 'bootloader-config.txt').write_text(
            '[all]\nBOOT_UART=0\nWAKE_ON_GPIO=1\nPOWER_OFF_ON_HALT=0\n'
            + ('NET_INSTALL_ENABLED=0\n' if configured else ''))
        (path / 'bootloader-version.txt').write_text(
            '57db150d63864d47e6c9071f6b086a5401eb4e92\nupdate-time 1700000000\n')
        (path / 'capture.json').write_text(json.dumps(dict(imported_utc='2026-10-10T01:00:00Z',
            sha256={name: resolver.file_hash(path / name) for name in provision.CAPTURE_FILES})))
        return path

    def built(self):
        plan, _ = self.configure()
        (self.output / 'images').mkdir()
        (self.output / 'images/sdcard.img').write_bytes(b'clean image')
        audit = workflow.new_audit(plan)
        audit.update(state='built', artifacts=workflow.artifact_manifest(self.output))
        workflow.save_audit(self.output, audit)
        return plan

    def test_matching_capture_skips_backend_but_records_evidence(self):
        plan = self.built()
        with patch.object(provision, 'run') as run:
            workflow.prepare_eeprom(self.ctx, plan, self.output, self.capture(True))
        run.assert_not_called()
        record = json.loads((self.output / 'profile/eeprom-result.json').read_text())
        self.assertEqual(record['status'], 'matches-saved-capture')
        self.assertFalse(Path(record['bundle']).is_absolute())
        audit = json.loads((self.output / 'profile/audit.json').read_text())
        self.assertEqual(audit['hardware'][0]['status'], 'unverified')
        workflow.assert_current(self.ctx, plan, self.output, built=True)
        (provision.bundle_for(self.output) / 'capture/bootloader-config.txt').write_text('changed')
        with self.assertRaisesRegex(UserError, 'artifact changed'):
            workflow.assert_current(self.ctx, plan, self.output, built=True)

    def test_selected_changes_and_build_images_reach_backend(self):
        plan = self.built()
        host = self.output / 'host/bin/mcopy'
        host.parent.mkdir(parents=True)
        host.touch()
        def run(args, **kwargs):
            bundle = Path(args[args.index('--output') + 1])
            bundle.mkdir(parents=True)
            (bundle / 'manifest.json').write_text('{}')
            self.assertEqual(args[args.index('--set') + 1], 'NET_INSTALL_ENABLED=0')
            self.assertEqual(Path(args[args.index('--base-images') + 1]), self.output / 'images')
            self.assertEqual(Path(args[args.index('--mcopy') + 1]), host)
        with patch.object(provision, 'run', side_effect=run) as backend:
            workflow.prepare_eeprom(self.ctx, plan, self.output, self.capture())
        backend.assert_called_once()
        self.assertEqual((self.output / 'images/sdcard.img').read_bytes(), b'clean image')
        self.assertEqual(json.loads((self.output / 'profile/eeprom-result.json').read_text())['status'], 'prepared')
        workflow.assert_current(self.ctx, plan, self.output, built=True)

    def test_no_capture_fails_before_configure_or_build(self):
        with patch.object(provision, 'capture_for', side_effect=UserError('missing capture')), \
                patch.object(workflow, 'configure') as configure, patch.object(workflow, 'build') as build:
            with self.assertRaisesRegex(UserError, 'missing capture'):
                workflow.execute(self.ctx, 'image', 'polis-rpi4-speed', self.output)
        configure.assert_not_called()
        build.assert_not_called()

    def test_image_order_and_preparation_failure_prevent_snapshot(self):
        events = []
        def stage(name):
            def call(*args, **kwargs):
                events.append(name)
                if name == 'prepare':
                    raise UserError('backend failed')
            return call
        with patch.object(provision, 'capture_for', return_value=self.capture()), \
                patch.object(workflow, 'configure', side_effect=stage('configure')), \
                patch.object(workflow, 'build', side_effect=stage('build')), \
                patch.object(workflow, 'prepare_eeprom', side_effect=stage('prepare')), \
                patch.object(snapshots, 'snapshot') as snapshot:
            with self.assertRaisesRegex(UserError, 'backend failed'):
                workflow.execute(self.ctx, 'image', 'polis-rpi4-speed', self.output)
        self.assertEqual(events, ['configure', 'build', 'prepare'])
        snapshot.assert_not_called()

    def test_capture_tampering_fails_before_preparation(self):
        capture = self.capture()
        (capture / 'bootloader-config.txt').write_text('NET_INSTALL_ENABLED=0\n')
        with self.assertRaisesRegex(UserError, 'changed EEPROM capture'):
            provision.capture_for(self.ctx, self.plan(), capture)

    def test_latest_capture_is_selected_and_base_needs_none(self):
        repo = self.root / 'repo'
        captures = repo / 'local/polis-wired-state/captures'
        captures.mkdir(parents=True)
        for name, timestamp in (('z-old', '2026-10-09T00:00:00Z'), ('a-new', '2026-10-10T00:00:00Z')):
            shutil.copytree(self.capture(), captures / name)
            path = captures / name / 'capture.json'
            data = json.loads(path.read_text()); data['imported_utc'] = timestamp
            path.write_text(json.dumps(data))
        ctx = replace(self.ctx, repo=repo)
        self.assertEqual(provision.capture_for(ctx, self.plan()).name, 'a-new')
        self.assertIsNone(provision.capture_for(ctx, self.plan('polis-rpi4-base')))

    def test_snapshot_saves_selected_bundle_and_rejects_tampering(self):
        plan = self.built()
        workflow.prepare_eeprom(self.ctx, plan, self.output, self.capture(True))
        (self.ctx.buildroot / 'Makefile').write_text('# fixture')
        repo = self.root / 'small-repo'; repo.mkdir()
        ctx = replace(self.ctx, repo=repo)
        with contextlib.redirect_stdout(io.StringIO()):
            saved = snapshots.snapshot(ctx, self.output, 'prepared')
        bundle = provision.bundle_for(saved)
        self.assertTrue((bundle / 'capture/bootloader-config.txt').is_file())
        self.assertIn(str(bundle.relative_to(saved)), (saved / 'SHA256SUMS').read_text())
        self.assertEqual(workflow.artifact_manifest(self.output), workflow.artifact_manifest(saved))

