"""Content-based build rules. Project/package policy lives in build.toml files."""
from contextlib import contextmanager
import fcntl
import fnmatch
import glob
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import tomllib

from ..common import UserError

STATE = '.build-state.json'
SCHEMA = 1


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


@contextmanager
def locked(output):
    output.mkdir(parents=True, exist_ok=True)
    with (output / '.build.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


class Tracker:
    def __init__(self, ctx, profile, output):
        self.ctx, self.output = ctx, output
        self.bindings = dict(repo=str(ctx.repo), buildroot=str(ctx.buildroot),
                             profile=profile, output=str(output))
        self.manifest = ctx.repo / 'build.toml'
        self.policy = self.read_policy(self.manifest)
        if self.policy.get('schema') != SCHEMA:
            raise UserError(f'{self.manifest}: expected schema = {SCHEMA}')
        self.steps = list(self.policy.get('steps', []))
        for pattern in self.policy.get('includes', []):
            for path in self.matches(pattern):
                self.steps.extend(self.read_policy(path).get('steps', []))
        for rule in self.steps:
            if not isinstance(rule, dict):
                raise UserError(f'{self.manifest}: rules must be TOML tables')
            for field in ('inputs', 'outputs', 'config', 'config_paths', 'depends_on', 'targets'):
                value = rule.get(field, [])
                if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
                    raise UserError(f'{self.manifest}: {field} must be a list of strings')
            if not rule.get('targets') or not rule.get('outputs'):
                raise UserError(f'{self.manifest}: every rule needs targets and outputs')
            if 'when' in rule and not isinstance(rule['when'], str):
                raise UserError(f'{self.manifest}: when must name a config symbol')
            for target in rule['targets']:
                if not re.fullmatch(r'[A-Za-z0-9_{][A-Za-z0-9_{}.-]*', target):
                    raise UserError(f'Invalid Make target in build rule: {target!r}')
        self.steps = self.ordered_steps(self.steps)
        self.state_path = output / STATE
        try:
            state = json.loads(self.state_path.read_text())
            self.previous = state['entries'] if state.get('schema') == SCHEMA else {}
            if not isinstance(self.previous, dict):
                self.previous = {}
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            self.previous = {}
        self.entries = {}
        self.invalidated = False

    @staticmethod
    def read_policy(path):
        try:
            with path.open('rb') as stream:
                return tomllib.load(stream)
        except (OSError, ValueError) as error:
            raise UserError(f'Cannot load build rules {path}: {error}') from error

    @staticmethod
    def ordered_steps(steps):
        by_name = {}
        for step in steps:
            name = step.get('name')
            if not isinstance(name, str) or not name or name in by_name or name in ('configure', 'image'):
                raise UserError(f'Invalid or duplicate build rule name: {name!r}')
            by_name[name] = step
        ordered, visiting, done = [], set(), set()

        def visit(name):
            if name not in by_name:
                raise UserError(f'Unknown build-rule dependency: {name}')
            if name in visiting:
                raise UserError(f'Cyclic build-rule dependency: {name}')
            if name in done:
                return
            visiting.add(name)
            for dep in by_name[name].get('depends_on', []):
                visit(dep)
            visiting.remove(name)
            done.add(name)
            ordered.append(by_name[name])

        for name in by_name:
            visit(name)
        return ordered

    def expand(self, value, config=None):
        def placeholders(text):
            def replace(match):
                name = match[1]
                if name not in self.bindings:
                    raise UserError(f'Unknown build-rule placeholder {name!r}')
                return self.bindings[name]
            return re.sub(r'(?<!\$)\{([^{}]+)\}', replace, text)

        value = placeholders(value)
        variables = {**(config or {}), **self.policy.get('variables', {})}
        for _ in range(20):
            names = re.findall(r'\$\(([^()]+)\)|\$\{([^{}]+)\}', value)
            if not names:
                return value
            for pair in names:
                name = next(x for x in pair if x)
                if name not in variables:
                    raise UserError(f'Unresolved path variable {name!r} in {value!r}')
                replacement = placeholders(variables[name])
                value = value.replace(f'$({name})', replacement).replace('${' + name + '}', replacement)
        raise UserError(f'Cyclic path variable in {value!r}')

    def matches(self, pattern, base=None):
        path = Path(self.expand(pattern))
        if not path.is_absolute():
            path = (base or self.ctx.repo) / path
        return [Path(p) for p in sorted(glob.glob(str(path), recursive=True))]

    def files_digest(self, patterns, *, outputs=False):
        records, complete = {}, True
        exclusions = [] if outputs else self.policy.get('exclude', [])

        def visit(path, ancestors=()):
            nonlocal complete
            if any(fnmatch.fnmatch(path.name, p) or fnmatch.fnmatch(str(path), p)
                   for p in exclusions):
                return
            key = str(path)
            if key in records:
                return
            if path.is_symlink():
                records[key] = ['symlink', os.readlink(path)]
                # Hash linked regular-file contents too (e.g. a linked config).
                if path.is_file():
                    with path.open('rb') as stream:
                        records[key].append(hashlib.file_digest(stream, 'sha256').hexdigest())
                elif path.is_dir() and not outputs:
                    resolved = path.resolve()
                    if resolved in ancestors:
                        raise UserError(f'Cyclic input directory symlink: {path}')
                    for child in sorted(path.iterdir()):
                        visit(child, (*ancestors, resolved))
                elif outputs and not path.exists():
                    complete = False
            elif path.is_dir():
                records[key] = ['directory', path.stat().st_mode & 0o777]
                for child in sorted(path.iterdir()):
                    visit(child, (*ancestors, path.resolve()))
            elif path.is_file():
                with path.open('rb') as stream:
                    records[key] = ['file', path.stat().st_mode & 0o777,
                                    hashlib.file_digest(stream, 'sha256').hexdigest()]
            else:
                records[key] = ['missing']
                complete = False

        for pattern in patterns:
            paths = self.matches(pattern)
            if not paths:
                records[pattern] = ['missing']
                complete = False
            for path in paths:
                visit(path)
        return digest(records), complete

    def fingerprint(self, rule, config, dependencies=None):
        defaults = self.policy.get('defaults', {})
        patterns = [*defaults.get('inputs', []), *rule.get('inputs', [])]
        for key in rule.get('config_paths', []):
            # Split before expanding: a repository path containing spaces is
            # still one filename when it comes from a Make variable.
            for token in shlex.split(config.get(key, '')):
                path = Path(self.expand(token, config))
                if not path.is_absolute():
                    path = self.ctx.buildroot / path
                patterns.append(str(path))
        selectors = [*defaults.get('config', []), *rule.get('config', [])]
        selected = {key: value for key, value in config.items()
                    if any(fnmatch.fnmatchcase(key, p) for p in selectors)}
        inputs, _ = self.files_digest(patterns)
        return digest(dict(rule=rule, inputs=inputs, config=selected,
                           variables=self.policy.get('variables', {}),
                           exclude=self.policy.get('exclude', []),
                           context=self.bindings, dependencies=dependencies or {}))

    def current(self, name, fingerprint, rule):
        outputs, complete = self.files_digest(rule['outputs'], outputs=True)
        previous = self.previous.get(name, {})
        return (complete and isinstance(previous, dict)
                and previous.get('inputs') == fingerprint
                and previous.get('outputs') == outputs)

    def invalidate(self):
        if not self.invalidated:
            # Invalidate BEFORE changing artifacts. If B partly builds then
            # fails, reverting inputs to cached A must not reuse B's artifacts.
            self.state_path.unlink(missing_ok=True)
            self.invalidated = True

    def remember(self, name, fingerprint, rule):
        self.entries[name] = dict(inputs=fingerprint, rule=rule)

    def commit(self):
        entries = {}
        for name, entry in self.entries.items():
            outputs, complete = self.files_digest(entry['rule']['outputs'], outputs=True)
            if complete:
                entries[name] = dict(inputs=entry['inputs'], outputs=outputs)
        temporary = self.state_path.with_suffix('.tmp')
        temporary.write_text(json.dumps(dict(schema=SCHEMA, entries=entries), indent=2) + '\n')
        temporary.replace(self.state_path)
