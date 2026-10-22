"""Synthetic selections for component tests, independent of shipped images."""
import json
from pathlib import Path
import shutil

from src.profiles.options import Group

REPO = Path(__file__).resolve().parents[1]
WIFI_GROUPS = (Group.WIFI_IWD, Group.WIFI_SETUPD)
DEVELOPMENT_GROUPS = (*WIFI_GROUPS, Group.DEVELOPMENT_ACCESS)
PREMERGE_GROUPS = (Group.POLIS_RUNTIME, Group.USB_PERIPHERAL_ONLY,
                   Group.USB_DIAGNOSTICS, Group.BLUETOOTH_UART_ROUTING,
                   Group.PREMERGE_OVERLAYS)


def write_profile(repo, name, groups=(), requires=()):
    path = repo / 'profiles' / f'{name}.toml'
    path.write_text(f'schema = 1\nname = {json.dumps(name)}\n'
                    'base = "polis-rpi4-v1"\n'
                    f'groups = {json.dumps(list(groups))}\n'
                    f'requires = {json.dumps(list(requires))}\n')
    return path


def prepare_profiles(repo):
    """Copy component definitions, never production profile selections."""
    profiles = repo / 'profiles'
    profiles.mkdir(parents=True)
    for name in ('base', 'groups'):
        shutil.copytree(REPO / 'profiles' / name, profiles / name, symlinks=True)
    for name in ('buildroot.lock.json', 'post-image.py'):
        shutil.copy(REPO / 'profiles' / name, profiles / name)
    write_profile(repo, 'fixture-empty')
    write_profile(repo, 'fixture-wifi', WIFI_GROUPS)
    write_profile(repo, 'fixture-development', DEVELOPMENT_GROUPS)
    write_profile(repo, 'fixture-eeprom', (Group.EEPROM_NO_NET_INSTALL,))


def prepare_repo(repo):
    prepare_profiles(repo)
    for name in ('src', 'board', 'build-support', 'package', 'patches',
                 'Config.in', 'external.mk', 'external.desc', 'build.toml'):
        (repo / name).symlink_to(REPO / name)
