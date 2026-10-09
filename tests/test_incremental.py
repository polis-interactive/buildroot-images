"""Incremental decisions against isolated sources and generated artifacts."""
import contextlib
from io import StringIO
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from src import builds, incremental, snapshots
from src.common import Context, UserError


POLICY = '''schema = 1
includes = ["package/*/build.toml"]
exclude = ["target", "*.pyc"]
[variables]
BR2_EXTERNAL_TEST_PATH = "{repo}"
[configure]
targets = ["{board}_defconfig"]
inputs = ["configs/{board}_defconfig"]
outputs = ["{output}/.config"]
[image]
targets = ["all"]
inputs = ["overlay"]
config = ["*"]
outputs = ["{output}/images/sdcard.img"]
[[steps]]
name = "application"
targets = ["application-clean", "application"]
inputs = ["app"]
config = ["BR2_APP*"]
config_paths = ["BR2_APP_FRAGMENT"]
outputs = ["{output}/application"]
[[steps]]
name = "consumer"
depends_on = ["application"]
targets = ["consumer"]
inputs = ["consumer"]
outputs = ["{output}/consumer"]
'''


class IncrementalTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name) / 'repo with spaces'
        self.br = Path(self.temp.name) / 'buildroot'
        self.repo.mkdir()
        self.br.mkdir()
        (self.repo / 'configs').mkdir()
        self.defconfig = self.repo / 'configs/test_defconfig'
        self.defconfig.write_text('BR2_APP=y\n')
        self.policy = self.repo / 'build.toml'
        self.policy.write_text(POLICY)
        (self.repo / 'app').mkdir()
        self.source = self.repo / 'app/main.rs'
        self.source.write_text('original')
        (self.repo / 'consumer').write_text('consumer')
        (self.repo / 'overlay').mkdir()
        self.ctx = Context(self.repo, self.br, self.br / 'dl', self.repo / 'snapshots')
        self.output = self.ctx.output('test')
        self.commands = []
        self.capture = self.enterContext(patch.object(snapshots, 'snapshot'))
        self.runner = self.enterContext(patch.object(builds, 'run', side_effect=self.run_make))
        self.enterContext(contextlib.redirect_stdout(StringIO()))

    def run_make(self, args):
        target = args[-1]
        self.commands.append(target)
        if target == 'test_defconfig':
            (self.output / '.config').write_text(
                f'BR2_DEFCONFIG="{self.defconfig}"\n' + self.defconfig.read_text())
        elif target == 'all':
            (self.output / 'images').mkdir(exist_ok=True)
            (self.output / 'images/sdcard.img').write_text('image')
        elif not target.endswith('-clean'):
            (self.output / target).write_text('built: ' + self.source.read_text())

    def build(self, **kwargs):
        self.commands.clear()
        builds.build(self.ctx, 'build', 'test', self.output, **kwargs)
        return self.commands[:]

    def tracker(self):
        return incremental.Tracker(self.ctx, 'test', self.output)

    def test_unchanged_build_skips_all_make_steps_but_still_snapshots(self):
        self.assertEqual(self.build(), ['test_defconfig', 'application-clean',
                                       'application', 'consumer', 'all'])
        self.assertEqual(self.build(), [])
        self.assertEqual(self.capture.call_count, 2)

    def test_content_change_with_same_mtime_rebuilds_dependents(self):
        self.build()
        before = self.source.stat()
        self.source.write_text('modified')
        os.utime(self.source, ns=(before.st_atime_ns, before.st_mtime_ns))
        self.assertEqual(self.build(), ['application-clean', 'application', 'consumer', 'all'])
        self.assertEqual(self.build(), [])

    def test_touch_and_excluded_generated_files_do_not_rebuild(self):
        self.build()
        os.utime(self.source, (1, 1))
        generated = self.repo / 'app/target'
        generated.mkdir()
        (generated / 'binary').write_text('generated')
        (self.repo / 'app/module.pyc').write_text('generated')
        self.assertEqual(self.build(), [])

    def test_added_deleted_and_mode_changed_sources_rebuild(self):
        self.build()
        extra = self.repo / 'app/extra.rs'
        extra.write_text('extra')
        self.assertIn('application', self.build())
        extra.unlink()
        self.assertIn('application', self.build())
        self.source.chmod(0o755)
        self.assertIn('application', self.build())

    def test_overlay_change_only_assembles_image(self):
        self.build()
        (self.repo / 'overlay/config').write_text('new')
        self.assertEqual(self.build(), ['all'])

    def test_config_change_reconfigures_and_rebuilds_selected_component(self):
        self.build()
        self.defconfig.write_text('BR2_APP=y\nBR2_APP_FEATURE=y\n')
        self.assertEqual(self.build(), ['test_defconfig', 'application-clean',
                                       'application', 'consumer', 'all'])
        self.defconfig.write_text(self.defconfig.read_text() + 'BR2_IMAGE_FEATURE=y\n')
        self.assertEqual(self.build(), ['test_defconfig', 'all'])

    def test_no_configure_preserves_unsaved_settings_then_normal_build_restores(self):
        self.build()
        config = self.output / '.config'
        config.write_text(config.read_text() + 'BR2_APP_FEATURE=y\n')
        self.assertEqual(self.build(configure=False),
                         ['application-clean', 'application', 'consumer', 'all'])
        self.assertIn('BR2_APP_FEATURE=y', config.read_text())
        self.assertEqual(self.build(), ['test_defconfig', 'application-clean',
                                       'application', 'consumer', 'all'])
        self.assertNotIn('BR2_APP_FEATURE=y', config.read_text())

    def test_missing_or_tampered_outputs_rebuild_dependents(self):
        self.build()
        (self.output / 'application').unlink()
        self.assertEqual(self.build(), ['application-clean', 'application', 'consumer', 'all'])
        (self.output / 'consumer').write_text('corrupt')
        self.assertEqual(self.build(), ['consumer', 'all'])
        (self.output / 'images/sdcard.img').unlink()
        self.assertEqual(self.build(), ['all'])

    def test_failed_partial_build_invalidates_even_if_inputs_are_reverted(self):
        self.build()
        self.source.write_text('new')
        self.capture.reset_mock()

        def fail(args):
            self.run_make(args)
            if args[-1] == 'consumer':
                raise subprocess.CalledProcessError(2, args)

        self.runner.side_effect = fail
        with self.assertRaises(subprocess.CalledProcessError):
            self.build()
        self.assertFalse((self.output / incremental.STATE).exists())
        self.capture.assert_not_called()
        self.source.write_text('original')
        self.runner.side_effect = self.run_make
        self.assertEqual(self.build(), ['test_defconfig', 'application-clean',
                                       'application', 'consumer', 'all'])

    def test_bad_or_deleted_state_rebuilds_safely(self):
        self.build()
        for state in ('null', '[]', '{broken', '{"schema":1,"entries":[]}'):
            with self.subTest(state=state):
                (self.output / incremental.STATE).write_text(state)
                self.assertIn('application', self.build())
        (self.output / incremental.STATE).unlink()
        self.assertIn('application', self.build())

    def test_input_edit_during_build_is_not_certified_or_snapshotted(self):
        def edit(args):
            self.run_make(args)
            if args[-1] == 'all':
                self.source.write_text('edited while compiling')

        self.runner.side_effect = edit
        with self.assertRaisesRegex(UserError, 'inputs changed during'):
            self.build()
        self.assertFalse((self.output / incremental.STATE).exists())
        self.capture.assert_not_called()

    def test_declarative_new_package_needs_no_python_changes(self):
        self.build()
        package = self.repo / 'package/new'
        package.mkdir(parents=True)
        (package / 'build.toml').write_text('''[[steps]]
name = "extra"
depends_on = ["consumer"]
targets = ["extra"]
inputs = ["package/new"]
outputs = ["{output}/extra"]
''')
        self.assertEqual(self.build(), ['test_defconfig', 'extra', 'all'])
        self.assertEqual(self.build(), [])
        (package / 'source.c').write_text('source')
        self.assertEqual(self.build(), ['extra', 'all'])

    def test_cycles_and_unknown_dependencies_rejected_before_build(self):
        for dependency in ('consumer', 'missing'):
            self.policy.write_text(POLICY.replace('name = "application"',
                                                 f'name = "application"\ndepends_on = ["{dependency}"]'))
            with self.subTest(dependency=dependency), self.assertRaises(UserError):
                self.build()
            self.assertEqual(self.commands, [])

    def test_config_paths_expand_both_make_variable_forms_and_relative_paths(self):
        fragment = self.repo / 'app/fragment'
        fragment.write_text('one')
        for variable in ('$(BR2_EXTERNAL_TEST_PATH)', '${BR2_EXTERNAL_TEST_PATH}'):
            self.defconfig.write_text(f'BR2_APP_FRAGMENT="{variable}/app/fragment"\n')
            self.build()
            fragment.write_text(fragment.read_text() + 'change')
            self.assertIn('application', self.build())
            self.assertEqual(self.build(), [])
        (self.br / 'fragment').write_text('one')
        self.defconfig.write_text('BR2_APP_FRAGMENT="fragment"\n')
        self.build()
        (self.br / 'fragment').write_text('two')
        self.assertIn('application', self.build())

    def test_linked_directory_contents_are_tracked(self):
        linked = Path(self.temp.name) / 'linked'
        linked.mkdir()
        (linked / 'source').write_text('one')
        (self.repo / 'app/linked').symlink_to(linked, target_is_directory=True)
        self.build()
        (linked / 'source').write_text('two')
        self.assertIn('application', self.build())

    def test_disabled_rule_does_not_run(self):
        self.policy.write_text(POLICY.replace('name = "application"',
                                             'name = "application"\nwhen = "BR2_DISABLED"'))
        self.assertEqual(self.build(), ['test_defconfig', 'consumer', 'all'])
        self.assertEqual(self.build(), [])

    def test_output_directory_owns_its_state(self):
        self.build()
        self.output = self.repo / 'another output'
        self.assertEqual(self.build(), ['test_defconfig', 'application-clean',
                                       'application', 'consumer', 'all'])
        self.assertEqual(self.build(), [])

    def test_state_contains_sha256_inputs_and_outputs(self):
        self.build()
        state = json.loads((self.output / incremental.STATE).read_text())
        for entry in state['entries'].values():
            self.assertRegex(entry['inputs'], r'^[0-9a-f]{64}$')
            self.assertRegex(entry['outputs'], r'^[0-9a-f]{64}$')

    def test_real_make_build_then_skip_then_rebuild_changed_source(self):
        (self.br / 'Makefile').write_text('''
.PHONY: test_defconfig application-clean application consumer all
test_defconfig:
	@printf 'BR2_DEFCONFIG="%s/configs/test_defconfig"\\n' "$(BR2_EXTERNAL)" > "$(O)/.config"
	@cat "$(BR2_EXTERNAL)/configs/test_defconfig" >> "$(O)/.config"
application-clean:
	@rm -f "$(O)/application"
application:
	@cp "$(BR2_EXTERNAL)/app/main.rs" "$(O)/application"
consumer:
	@cp "$(O)/application" "$(O)/consumer"
all:
	@mkdir -p "$(O)/images"
	@cp "$(O)/consumer" "$(O)/images/sdcard.img"
''')
        self.runner.side_effect = lambda args: subprocess.run(args, check=True, capture_output=True)
        self.build()
        image = self.output / 'images/sdcard.img'
        self.assertEqual(image.read_text(), 'original')
        self.runner.reset_mock()
        self.build()
        self.runner.assert_not_called()
        self.source.write_text('updated source')
        self.build()
        self.assertEqual(image.read_text(), 'updated source')
        self.assertEqual([call.args[0][-1] for call in self.runner.call_args_list],
                         ['application-clean', 'application', 'consumer', 'all'])
