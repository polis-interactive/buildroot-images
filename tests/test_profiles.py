"""Profile input boundaries and enum/schema consistency."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from src.common import UserError
from src.profiles.options import Base, Capability, Group
from src.profiles.schema import SCHEMAS, schema_documents
from src.profiles import load_profile


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "profile.toml"
        self.example = (SCHEMAS / "examples/profile.toml").read_text()

    def load(self, text):
        self.path.write_text(text)
        return load_profile(self.path)

    def test_example_loads_as_enum_members(self):
        profile = self.load(self.example)
        self.assertIs(profile.base, Base.POLIS_RPI4_V1)
        self.assertTrue(all(type(group) is Group for group in profile.groups))
        self.assertTrue(all(type(cap) is Capability for cap in profile.requires))
        self.assertIn(Group.LZ4_KERNEL, profile.groups)

    def test_base_only_profile(self):
        profile = self.load('schema = 1\nname = "base-only"\n'
                            'base = "polis-rpi4-v1"\nrequires = []\ngroups = []\n')
        self.assertEqual(profile.groups, ())
        self.assertEqual(profile.requires, ())

    def test_invalid_selections_fail_with_location(self):
        cases = [
            ('"lz4-kernel"', '"lz4-kernal"', 'unknown Group'),
            ('"polis-rpi4-v1"', '"polis-rpi4-v2"', 'unknown Base'),
            ('"boot-diagnostics", "uart-diagnostics"', '"gipo", "uart-diagnostics"', 'unknown Capability'),
            ('"lz4-kernel"', '"lz4-kernel", "lz4-kernel"', 'duplicate'),
            ('"lz4-kernel"', '"static-core"', 'inherited from the base'),
            ('"lz4-kernel"', '42', 'expected a Group string'),
            ('schema = 1', 'schema = true', 'schema must be integer 1'),
            ('schema = 1', 'schema = 2', 'schema must be integer 1'),
            ('schema = 1', 'schema = 1\ntypo = true', 'unknown fields: typo'),
            ('schema = 1', '', 'missing fields: schema'),
            ('name = "rpi4-speed"', 'name = "../outside"', 'lowercase identifier'),
            ('"boot-diagnostics", "uart-diagnostics"', '"boot-diagnostics", "boot-diagnostics"', 'duplicate'),
        ]
        for old, new, message in cases:
            with self.subTest(message=message, replacement=new):
                with self.assertRaisesRegex(UserError, message) as raised:
                    self.load(self.example.replace(old, new))
                self.assertIn(str(self.path), str(raised.exception))

    def test_invalid_toml_is_actionable(self):
        with self.assertRaises(UserError) as raised:
            self.load('groups = [')
        self.assertIn(str(self.path), str(raised.exception))

    def test_checked_in_schemas_match_enums(self):
        for name, expected in schema_documents().items():
            with self.subTest(schema=name):
                actual = json.loads((SCHEMAS / f"{name}-v1.schema.json").read_text())
                self.assertEqual(actual, expected, 'Run python -m src.profiles.schema')

    def test_editor_directives_resolve(self):
        for name in ('base', 'profile'):
            path = SCHEMAS / f"examples/{name}.toml"
            header = path.read_text().splitlines()[0]
            self.assertTrue(header.startswith('#:schema '))
            self.assertEqual((path.parent / header.removeprefix('#:schema ')).resolve(),
                             SCHEMAS / f"{name}-v1.schema.json")

    def test_example_audit_input_hashes_remain_current(self):
        audit = json.loads((SCHEMAS / 'examples/audit.json').read_text())
        repo = Path(__file__).resolve().parents[1]
        for item in audit['inputs']:
            self.assertEqual(hashlib.sha256((repo / item['path']).read_bytes()).hexdigest(),
                             item['sha256'])
        self.assertEqual(hashlib.sha256(json.dumps(audit['inputs'], sort_keys=True).encode()).hexdigest(),
                         audit['plan_sha256'])


if __name__ == '__main__':
    unittest.main()
