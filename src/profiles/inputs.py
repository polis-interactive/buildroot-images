"""Shared Buildroot source lock and per-configuration provenance."""
import json
import re
import subprocess

from ..common import UserError, run

LOCK_PATH = 'profiles/buildroot.lock.json'


def source_date_epoch(buildroot):
    # The locked Buildroot release already owns this value; do not duplicate it.
    text = (buildroot / 'Makefile').read_text()
    match = re.search(r'^BR2_VERSION_EPOCH\s*=\s*(\d+)\s*$', text, re.MULTILINE)
    if not match:
        raise UserError(f'{buildroot}/Makefile: missing numeric BR2_VERSION_EPOCH')
    return int(match[1])


def verify_inputs(ctx, plan):
    from .resolver import digest
    path = ctx.repo / LOCK_PATH
    pins = json.loads(path.read_text())
    fields = {'schema', 'buildroot_url', 'buildroot_archive_sha256', 'buildroot_inputs_sha256'}
    if (set(pins) != fields or type(pins.get('schema')) is not int or pins['schema'] != 1
            or not isinstance(pins['buildroot_url'], str)
            or not pins['buildroot_url'].startswith('https://')
            or any(not isinstance(pins[key], str) or not re.fullmatch(r'[0-9a-f]{64}', pins[key])
                   for key in ('buildroot_archive_sha256', 'buildroot_inputs_sha256'))):
        raise UserError(f'{path}: invalid Buildroot lock')
    actual = digest([entry for entry in plan.inputs if entry['root'] == 'buildroot'])
    if actual != pins['buildroot_inputs_sha256']:
        raise UserError(f'Buildroot inputs differ from {LOCK_PATH}; '
                        'use the pinned Buildroot tree or review and update the lock')
    return pins


def provenance(ctx, plan, pins):
    from .resolver import digest
    def git(*args):
        try:
            return run(['git', '-C', ctx.repo, *args], capture=True).stdout.strip()
        except subprocess.CalledProcessError:
            return 'unavailable'
    result = dict(schema=1, profile=plan.profile.name, buildrootimages_revision=git('rev-parse', 'HEAD'),
                  buildrootimages_dirty=bool(git('status', '--porcelain', '--untracked-files=normal')),
                  plan_sha256=plan.fingerprint, pins=pins, source_date_epoch=plan.source_date_epoch)
    if plan.ssh_public_key:
        result['public_key_sha256'] = digest(plan.ssh_public_key)
    return result
