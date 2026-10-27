"""Resolve declarative groups into owned configuration and reproducible inputs."""
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import re
import tomllib

from ..common import Context, UserError
from .loader import Profile, load_profile
from .options import BASE_GROUPS, Capability, Group

DOMAINS = ('buildroot', 'kernel', 'busybox', 'firmware', 'cmdline', 'rootfs', 'image', 'eeprom')
REPO_PATH = '$(BR2_EXTERNAL_POLIS_PATH)/'
GENERATED = '$(BASE_DIR)/profile/'


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def file_hash(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def read_kconfig(path):
    result = {}
    for line in Path(path).read_text().splitlines():
        if match := re.fullmatch(r'# ((?:BR2|CONFIG)_\w+) is not set', line):
            result[match[1]] = 'n'
        elif match := re.fullmatch(r'((?:BR2|CONFIG)_\w+)=(.*)', line):
            value = match[2]
            result[match[1]] = json.loads(value) if value.startswith('"') else value
    return result


def kconfig_text(values):
    lines = []
    for key, value in sorted(values.items()):
        if value == 'n':
            lines.append(f'# {key} is not set')
        else:
            encoded = value if value in ('y', 'm') or re.fullmatch(r'\d+|0x[0-9a-fA-F]+', value) else json.dumps(value)
            lines.append(f'{key}={encoded}')
    return '\n'.join(lines) + '\n'


def profile_path(repo, name):
    if not re.fullmatch(r'[a-z][a-z0-9]*(?:-[a-z0-9]+)*', name):
        raise UserError('Invalid profile name')
    path = repo / 'profiles' / f'{name}.toml'
    if not path.is_file():
        raise UserError(f'Unknown profile {name!r}; use profiles to list choices')
    return path


def list_profiles(repo):
    return sorted(p.stem for p in (repo / 'profiles').glob('*.toml'))


def input_manifest(ctx, path):
    """Hash source content/modes, not timestamps or generated build directories."""
    result = []
    repo_paths = [ctx.repo / name for name in
             ('src', 'board', 'build-support', 'package', 'patches', 'Config.in',
              'external.mk', 'external.desc', 'build.toml')]
    repo_paths += [path, ctx.repo / 'profiles/base', ctx.repo / 'profiles/post-image.py',
                   ctx.repo / 'profiles/buildroot.lock.json']
    selected = {*BASE_GROUPS, *load_profile(path).groups}
    for group in Group:
        if group not in selected:
            continue
        repo_paths.append(ctx.repo / 'profiles/groups' / f'{group.value}.toml')
        payload = ctx.repo / 'profiles/groups' / group.value
        if payload.exists():
            repo_paths.append(payload)
        data = load_group(ctx.repo, group)
        for field in ('inputs', 'overlays', 'post_build'):
            repo_paths.extend(ctx.repo / name for name in data.get(field, []))
    roots = [(item, ctx.repo, 'repo') for item in repo_paths]
    roots += [(ctx.buildroot / name, ctx.buildroot, 'buildroot') for name in
              ('Makefile', 'Config.in', 'Config.in.legacy', 'arch', 'toolchain',
               'linux', 'package', 'system', 'fs', 'support/scripts', 'support/kconfig',
               'configs/raspberrypi4_64_defconfig', 'board/raspberrypi', 'board/raspberrypi4-64')]
    seen = set()

    def visit(item, root, label):
        if item.name in ('target', '.target', '__pycache__', '.git') or item.suffix in ('.pyc', '.md'):
            return
        if item in seen:
            return
        seen.add(item)
        relative = item.relative_to(root).as_posix()
        if item.is_symlink():
            # Target-root absolute symlinks are data, not host files to follow.
            result.append(dict(root=label, path=relative, sha256=digest(os.readlink(item))))
        elif item.is_dir():
            for child in sorted(item.iterdir()):
                visit(child, root, label)
        elif item.is_file():
            result.append(dict(root=label, path=relative, sha256=file_hash(item), mode=item.stat().st_mode & 0o777))
        else:
            raise UserError(f'Missing profile input: {item}')

    for item, root, label in roots:
        visit(item, root, label)
    return result


def load_group(repo, group):
    path = repo / 'profiles/groups' / f'{group.value}.toml'
    data = tomllib.loads(path.read_text())
    lists = {'requires', 'provides', 'disables', 'inputs', 'post_build', 'overlays',
             'remove_buildroot', 'remove_cmdline', 'remove_firmware', 'remove_overlays', 'firmware_overlays', 'evidence'}
    tables = {'buildroot', 'kernel', 'busybox', 'firmware', 'cmdline', 'eeprom'}
    unknown = data.keys() - lists - tables - {'schema', 'id', 'description'}
    if unknown or type(data.get('schema')) is not int or data['schema'] != 1 or data.get('id') != group.value:
        raise UserError(f'{path}: invalid group schema or fields: {sorted(unknown)}')
    if not isinstance(data.get('description'), str) or not data['description'].strip():
        raise UserError(f'{path}: description must be a nonempty string')
    for key in lists & data.keys():
        if not isinstance(data[key], list) or not all(isinstance(x, str) for x in data[key]):
            raise UserError(f'{path}: {key} must be a string array')
    for key in tables & data.keys():
        if not isinstance(data[key], dict) or not all(isinstance(v, str) for v in data[key].values()):
            raise UserError(f'{path}: {key} must map settings to strings')
    for name in data.get('provides', []) + data.get('disables', []):
        Capability(name)
    for dependency in data.get('requires', []):
        Group(dependency)
    return data


@dataclass
class Plan:
    profile: Profile
    groups: dict
    values: dict
    changes: list
    inputs: list
    fingerprint: str
    overlays: list
    post_build: list
    development: bool = False
    ssh_public_key: str = ''
    source_date_epoch: int = 0
    wifi_password: str = field(default='', repr=False)

    def enabled(self, group):
        return group.value in self.groups


def resolve(ctx: Context, name: str, *, development=False, ssh_public_key=None) -> Plan:
    selected_profile_path = profile_path(ctx.repo, name)
    profile = load_profile(selected_profile_path)
    ssh_key_text = ''
    if Group.DEVELOPMENT_ACCESS in profile.groups:
        from .development import public_key
        key_path = ssh_public_key or ctx.repo / '.local/development-ssh/id_ed25519.pub'
        if Path(key_path).is_file():
            ssh_key_text = public_key(key_path)
        elif ssh_public_key:
            raise UserError(f'Missing SSH public key: {key_path}')
    elif ssh_public_key:
        raise UserError('--ssh-public-key requires the development-access group')
    if profile.name != name:
        raise UserError(f'{selected_profile_path}: profile name must match its filename')
    upstream = ctx.repo / 'profiles/base/upstream_defconfig'
    actual = ctx.buildroot / 'configs/raspberrypi4_64_defconfig'
    if upstream.read_bytes() != actual.read_bytes():
        raise UserError('Buildroot upstream defconfig differs from the pinned profile base')
    selected = [*BASE_GROUPS, *(g for g in Group if g in profile.groups)]
    groups = {g.value: load_group(ctx.repo, g) for g in selected}
    ordered, visiting = [], set()

    def visit(name):
        if name in visiting:
            raise UserError(f'Cyclic group dependency: {name}')
        if name in ordered:
            return
        visiting.add(name)
        for dependency in groups[name].get('requires', []):
            if dependency not in groups:
                raise UserError(f'{name} requires explicitly selected group {dependency}')
            visit(dependency)
        visiting.remove(name)
        ordered.append(name)
    for group in groups:
        visit(group)
    provided = {c for g in groups.values() for c in g.get('provides', [])}
    disabled = {c for g in groups.values() for c in g.get('disables', [])}
    for capability in profile.requires:
        if capability.value in disabled or capability.value not in provided:
            raise UserError(f'Required capability {capability.value} is disabled or has no selected provider')
    if provided & disabled:
        raise UserError(f'Conflicting capabilities: {sorted(provided & disabled)}')
    values = {domain: {} for domain in DOMAINS}
    values['buildroot'] = read_kconfig(upstream)
    for line in (ctx.repo / 'profiles/base/config.txt').read_text().splitlines():
        if line and not line.startswith('#') and '=' in line:
            key, value = line.split('=', 1)
            if key == 'dtoverlay':
                key, value = 'overlay:' + value, 'y'
            values['firmware'][key] = value
    for token in (ctx.repo / 'profiles/base/cmdline.txt').read_text().split():
        key, _, value = token.partition('=')
        if key == 'console' and key in values['cmdline']:
            value = values['cmdline'][key] + ' ' + value
        values['cmdline'][key] = value
    changes, owners, overlays, post_build = [], {}, [], []

    def assign(domain, key, value, group, derived=False):
        owner_key = domain, key
        old = values[domain].get(key)
        previous = owners.get(owner_key)
        if previous and old != value:
            raise UserError(f'{domain}.{key}: conflicting assignments from {previous} and {group}')
        owners[owner_key] = group
        if old == value:
            for change in reversed(changes):
                if (change['domain'], change['key']) == owner_key:
                    if group not in change['owners']:
                        change['owners'].append(group)
                    break
            return
        changes.append(dict(comparison='upstream-to-base' if Group(group) in BASE_GROUPS else 'base-to-profile',
                            stage='requested', domain=domain, key=key, before=old, after=value,
                            owners=[group], basis='direct', caused_by=[],
                            reason=groups[group]['description'], payloads=[]))
        if value is None:
            values[domain].pop(key, None)
        else:
            values[domain][key] = value

    for group in ordered:
        data = groups[group]
        for source in data.get('inputs', []):
            path = ctx.repo / source
            files = sorted(path.rglob('*')) if path.is_dir() else [path]
            payloads = [dict(root='repo', path=str(p.relative_to(ctx.repo)),
                             sha256=digest(os.readlink(p)) if p.is_symlink() else file_hash(p))
                        for p in files if (p.is_file() or p.is_symlink())
                        and p.suffix not in ('.md', '.pyc')
                        and not any(part in ('target', '.target', '__pycache__', '.git') for part in p.parts)]
            if not path.exists():
                raise UserError(f'{group}: missing input {source}')
            changes.append(dict(comparison='upstream-to-base' if Group(group) in BASE_GROUPS else 'base-to-profile',
                                stage='requested', domain='image', key='source:' + source,
                                before=None, after=digest(payloads), owners=[group], basis='direct',
                                caused_by=[], reason=data['description'], payloads=payloads))
        for domain in ('buildroot', 'kernel', 'busybox', 'firmware', 'cmdline', 'eeprom'):
            for key, value in data.get(domain, {}).items():
                assign(domain, key, value, group)
        for domain in ('buildroot', 'firmware', 'cmdline'):
            for key in data.get('remove_' + domain, []):
                assign(domain, key, None, group)
        for overlay in data.get('remove_overlays', []):
            assign('firmware', 'overlay:' + overlay, None, group)
        for overlay in data.get('firmware_overlays', []):
            assign('firmware', 'overlay:' + overlay, 'y', group)
        for overlay in data.get('overlays', []):
            overlays.append(overlay)
            # Ordered, explicit overlay replacement is owned by this group.
            for file in sorted((ctx.repo / overlay).rglob('*')):
                if file.is_file() or file.is_symlink():
                    key = str(file.relative_to(ctx.repo / overlay))
                    before = values['rootfs'].get(key)
                    after = digest(os.readlink(file)) if file.is_symlink() else file_hash(file)
                    previous = owners.get(('rootfs', key))
                    if previous and Group(previous) not in BASE_GROUPS and before != after:
                        raise UserError(f'rootfs.{key}: conflicting overlays from {previous} and {group}')
                    owners['rootfs', key] = group
                    changes.append(dict(comparison='upstream-to-base' if Group(group) in BASE_GROUPS else 'base-to-profile',
                                        stage='requested', domain='rootfs', key=key, before=before, after=after,
                                        owners=[group], basis='direct', caused_by=[], reason=data['description'],
                                        payloads=[dict(root='repo', path=str(file.relative_to(ctx.repo)), sha256=after)]))
                    values['rootfs'][key] = after
        for hook in data.get('post_build', []):
            post_build.append(hook)
            assign('image', 'post-build:' + hook, file_hash(ctx.repo / hook), group)
    # Assembly choices have named owners, not hidden board-name branches.
    for group, key, value in (
        ('lz4-kernel', 'kernel-payload', 'lz4'), ('premerge-overlays', 'device-tree', 'premerged'),
        ('cutdown-firmware', 'firmware-variant', 'cutdown'),
        ('boot-ramdisk', 'boot-layout', 'ramdisk')):
        if group in groups:
            assign('image', key, value, group)
    if development:
        assign('firmware', 'bootloader_update', '0', 'erofs-root')
    inputs = input_manifest(ctx, selected_profile_path)
    from .inputs import source_date_epoch
    from .wifi import password_input
    wifi_password = password_input(ctx.repo) if 'wifi-setupd' in ordered else ''
    fingerprint = digest(dict(inputs=inputs, profile=profile.name, groups=ordered,
                              buildroot=str(ctx.buildroot), repo=str(ctx.repo), development=development,
                              ssh_public_key=ssh_key_text, wifi_password_sha256=digest(wifi_password)))
    plan = Plan(profile, {g: groups[g] for g in ordered}, values, changes, inputs,
                fingerprint, overlays, post_build, development, ssh_key_text,
                source_date_epoch(ctx.buildroot), wifi_password)
    compose_buildroot(plan)
    return plan


def compose_buildroot(plan):
    """Record generated paths/hook composition as owned changes too."""
    values = plan.values['buildroot']

    def assign(key, value, owners, comparison='upstream-to-base'):
        before = values.get(key)
        values[key] = value
        if before != value:
            plan.changes.append(dict(comparison=comparison, stage='requested', domain='buildroot',
                                     key=key, before=before, after=value, owners=owners,
                                     basis='direct', caused_by=[], reason='Compose generated profile inputs.', payloads=[]))

    for key, filename, owner in (
        ('BR2_LINUX_KERNEL_CONFIG_FRAGMENT_FILES', 'kernel.fragment', 'erofs-root'),
        ('BR2_PACKAGE_BUSYBOX_CONFIG_FRAGMENT_FILES', 'busybox.fragment', 'static-core'),
        ('BR2_PACKAGE_RPI_FIRMWARE_CONFIG_FILE', 'config.txt', 'erofs-root'),
        ('BR2_PACKAGE_RPI_FIRMWARE_CMDLINE_FILE', 'cmdline.txt', 'erofs-root'),
    ):
        assign(key, GENERATED + filename, [owner])
    assign('BR2_ROOTFS_POST_IMAGE_SCRIPT', REPO_PATH + 'profiles/post-image.py', ['erofs-root'])
    base_overlays = [path for g in BASE_GROUPS for path in plan.groups[g.value].get('overlays', [])]
    assign('BR2_ROOTFS_OVERLAY', ' '.join(REPO_PATH + p for p in base_overlays), ['erofs-root'])
    overlays = [g for g, data in plan.groups.items() if Group(g) not in BASE_GROUPS and data.get('overlays')]
    if overlays:
        assign('BR2_ROOTFS_OVERLAY', ' '.join(REPO_PATH + p for p in plan.overlays), overlays, 'base-to-profile')
    upstream_hook = 'board/raspberrypi4-64/post-build.sh'
    static_hook = REPO_PATH + 'board/rpi4/check-static.py'
    assign('BR2_ROOTFS_POST_BUILD_SCRIPT', upstream_hook + ' ' + static_hook, ['static-core'])
    hooks = [] if plan.enabled(Group.HEADLESS) else [upstream_hook]
    hooks += [REPO_PATH + p for p in plan.post_build if not p.endswith('check-static.py')]
    hooks.append(static_hook)
    owners = [g for g, data in plan.groups.items() if Group(g) not in BASE_GROUPS and data.get('post_build')]
    if plan.enabled(Group.HEADLESS):
        owners.append(Group.HEADLESS.value)
    if owners:
        assign('BR2_ROOTFS_POST_BUILD_SCRIPT', ' '.join(hooks), owners, 'base-to-profile')
    # All profiles share the output tree. Retire old USB workers even when
    # switching away from the screen profile, before rootfs image generation.
    assign('BR2_ROOTFS_POST_BUILD_SCRIPT', values['BR2_ROOTFS_POST_BUILD_SCRIPT'] +
           ' ' + REPO_PATH + 'board/rpi4/install-controller-usb.py' +
           ' ' + REPO_PATH + 'package/polis-firstboot/install.py', ['erofs-root'])
    if not plan.development:
        assign('BR2_PACKAGE_POLIS_FIRSTBOOT', 'y', ['erofs-root'])


def write_generated(ctx, plan, output):
    directory = output / 'profile'
    directory.mkdir(parents=True, exist_ok=True)
    def write(name, text):
        path = directory / name
        if not path.is_file() or path.read_text() != text:
            path.write_text(text)
    selected = plan.values
    firmware = dict(selected['firmware'])
    premerge = plan.enabled(Group.PREMERGE_OVERLAYS)
    firmware_overlays = [key[8:] for key in firmware if key.startswith('overlay:')]
    lines = [f'{key}={value}' for key, value in firmware.items() if not key.startswith('overlay:')]
    if not premerge:
        lines.extend('dtoverlay=' + overlay for overlay in firmware_overlays)
    write('config.txt', '\n'.join(lines) + '\n')
    tokens = []
    for key, value in selected['cmdline'].items():
        tokens += [key + ('=' + v if v else '') for v in (value.split() if key == 'console' else [value])]
    write('cmdline.txt', ' '.join(tokens) + '\n')
    for domain in ('kernel', 'busybox'):
        write(f'{domain}.fragment', kconfig_text(selected[domain]))
    buildroot = dict(selected['buildroot'])
    write('defconfig', kconfig_text(buildroot))
    if plan.ssh_public_key:
        write('authorized_keys', plan.ssh_public_key)
    if plan.enabled(Group.WIFI_SETUPD):
        path = directory / 'wifi-password'
        # Do not include the plaintext in configuration/audit JSON or logs.
        if not path.is_file() or path.read_text() != plan.wifi_password:
            with path.open('w') as stream:
                os.fchmod(stream.fileno(), 0o600)
                stream.write(plan.wifi_password)
    write('image-plan.json', json.dumps(dict(
        wifi_setup=plan.enabled(Group.WIFI_SETUPD),
        lz4=plan.enabled(Group.LZ4_KERNEL), premerge=premerge,
        firmware_overlays=firmware_overlays,
        cutdown=plan.enabled(Group.CUTDOWN_FIRMWARE),
        ramdisk=plan.enabled(Group.BOOT_RAMDISK), eeprom=selected['eeprom'],
        development=plan.development,
        reproducible=selected['buildroot'].get('BR2_REPRODUCIBLE') == 'y',
        source_date_epoch=plan.source_date_epoch,
        profile=plan.profile.name, fingerprint=plan.fingerprint), indent=2) + '\n')
    return buildroot
