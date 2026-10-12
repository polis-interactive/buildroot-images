"""Profile configuration, staged builds, automatic images, and saved audits."""
import json
from pathlib import Path
import shutil

from ..artifacts import snapshots
from ..build import incremental
from ..common import Context, UserError, run
from . import inputs, provision
from .options import BASE_GROUPS, Group
from .resolver import DOMAINS, digest, file_hash, read_kconfig, resolve, write_generated


def output_for(ctx, plan, explicit=None):
    # Different input sets never inherit removed packages or old kernel objects.
    mode = '-development' if plan.development else ''
    return (explicit or ctx.repo / '.output' / f'{plan.profile.name}{mode}-{plan.fingerprint[:12]}').resolve()


def make_args(ctx, output):
    args = ['make', '-C', str(ctx.buildroot), f'O={output}', f'BR2_EXTERNAL={ctx.repo}',
            f'BR2_DL_DIR={ctx.downloads}', f'BR2_DEFCONFIG={output / "profile/defconfig"}']
    provenance = output / 'profile/build-inputs.json'
    if provenance.is_file():
        record = json.loads(provenance.read_text())
        args.append(f'SOURCE_DATE_EPOCH={record["source_date_epoch"]}')
    return args


def generated_hashes(output):
    files = ['defconfig', 'kernel.fragment', 'busybox.fragment', 'config.txt',
             'cmdline.txt', 'image-plan.json']
    result = {f'profile/{name}': file_hash(output / 'profile' / name) for name in files}
    for name in ('authorized_keys', 'build-inputs.json'):
        if (output / 'profile' / name).is_file():
            result[f'profile/{name}'] = file_hash(output / 'profile' / name)
    result['.config'] = file_hash(output / '.config')
    return result


def save_json(path, value):
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, indent=2) + '\n')
    temporary.replace(path)


def artifact_manifest(output):
    files = [path for path in (output / 'images').rglob('*') if path.is_file()]
    record = output / 'profile/eeprom-result.json'
    if record.is_file():
        files.append(record)
    bundle = provision.bundle_for(output)
    if bundle:
        files.extend(path for path in bundle.rglob('*') if path.is_file())
    return [dict(root='output', path=str(path.relative_to(output)), sha256=file_hash(path))
            for path in sorted(files)]


def render(audit):
    comparison = 'base-to-profile' if any(g['selected_by'] == 'profile' for g in audit['groups']) else 'upstream-to-base'
    baseline = 'the musl/EROFS/static base' if comparison == 'base-to-profile' else 'upstream Buildroot'
    lines = [f'Profile: {audit["profile"]}', f'Base: {audit["base"]}', f'State: {audit["state"]}',
             f'Plan: {audit["plan_sha256"]}', '', f'Differences from {baseline}:']
    if 'provisioning' in audit:
        lines.insert(3, 'First-boot setup: ' + ('enabled (final)' if audit['provisioning']['enabled']
                                             else 'disabled (development)'))
    for change in audit['changes']:
        if change['comparison'] == comparison:
            lines.append(f'  {change["domain"]}.{change["key"]}: {change["before"]!r} -> {change["after"]!r}'
                         f' [{", ".join(change["owners"])}; {change["stage"]}]')
    if not any(c['comparison'] == comparison for c in audit['changes']):
        lines.append('  None.')
    lines += ['', 'Checks:']
    lines += [f'  {c["id"]}: {c["status"]} — {c["detail"]}' for c in audit['checks']]
    if audit['hardware']:
        lines += ['', 'Board EEPROM settings (verified on the target during provisioning):']
        lines += [f'  {h["key"]}={h["expected"]}: {h["status"]}' for h in audit['hardware']]
    lines += ['', 'audit.json also records base-to-upstream changes and source ownership.',
              'Requested changes are not a claim that all dependency-induced effective changes were attributed.']
    return '\n'.join(lines) + '\n'


def save_audit(output, audit):
    save_json(output / 'profile/audit.json', audit)
    (output / 'profile/audit.txt').write_text(render(audit))


def new_audit(plan):
    return dict(schema=1, example=False, state='planned', profile=plan.profile.name,
                base=plan.profile.base.value, resolver='polis-profiles-v1', plan_sha256=plan.fingerprint,
                provisioning=dict(enabled=not plan.development, mode='development' if plan.development else 'final'),
                inputs=plan.inputs, groups=[dict(id=name,
                    selected_by='base' if Group(name) in BASE_GROUPS else 'profile', evidence=data.get('evidence', []))
                    for name, data in plan.groups.items()], changes=plan.changes.copy(),
                coverage=[dict(comparison=comparison, domain=domain, status='pending',
                               detail='Requested settings and source payloads recorded; effective dependency comparison pending.')
                          for comparison in ('upstream-to-base', 'base-to-profile') for domain in DOMAINS],
                checks=[dict(id='buildroot-selections', phase='configuration', status='pending', detail='Awaiting Buildroot configuration.'),
                        dict(id='kernel-busybox-selections', phase='artifact', status='pending', detail='Verified against component configs during build.'),
                        dict(id='static-core', phase='artifact', status='pending', detail='Post-build ELF check is mandatory.'),
                        dict(id='image-assembly', phase='artifact', status='pending', detail='Awaiting genimage.')],
                hardware=[dict(key=key, expected=value, status='unverified', evidence=[])
                          for key, value in plan.values['eeprom'].items()], artifacts=[])


def check_values(expected, actual, label, repo, output):
    failures = []
    for key, value in expected.items():
        value = value.replace('$(BR2_EXTERNAL_POLIS_PATH)', str(repo)).replace('$(BASE_DIR)', str(output))
        observed = actual.get(key, 'n')
        observed = observed.replace('$(BR2_EXTERNAL_POLIS_PATH)', str(repo)).replace('$(BASE_DIR)', str(output))
        if observed != value:
            failures.append(f'{key}: requested {value!r}, effective {observed!r}')
    if failures:
        raise UserError(f'{label} did not retain requested settings:\n' + '\n'.join(failures))


def assert_current(ctx, plan, output, built=False):
    path = output / 'profile/state.json'
    if not path.is_file():
        raise UserError(f'{output}: configure this profile first')
    state = json.loads(path.read_text())
    if state['fingerprint'] != plan.fingerprint:
        raise UserError('Profile inputs changed after configuration; configure again in a fresh output directory')
    if state['generated'] != generated_hashes(output):
        raise UserError('Generated configuration changed after review; configure again')
    if built:
        audit = json.loads((output / 'profile/audit.json').read_text())
        if audit['state'] != 'built' or not audit['artifacts']:
            raise UserError('Profile has no completed, verified build to snapshot')
        if artifact_manifest(output) != audit['artifacts']:
            raise UserError('Built artifact changed since validation; rebuild before snapshotting')


def configure(ctx, plan, output, display):
    pins = inputs.verify_inputs(ctx, plan)
    if plan.enabled(Group.DEVELOPMENT_ACCESS):
        if not plan.ssh_public_key:
            raise UserError('Development image requires an SSH public key; use the image/configure command')
    state = output / 'profile/state.json'
    owner = output / 'profile/owner.json'
    if (output / '.config').exists():
        if not owner.exists() or json.loads(owner.read_text())['fingerprint'] != plan.fingerprint:
            raise UserError('Output belongs to another input set; choose a fresh --output or use the default')
    if state.is_file() and (output / 'profile/audit.json').is_file():
        try:
            assert_current(ctx, plan, output)
        except (UserError, OSError):
            pass  # Regenerate missing or changed config files below.
        else:
            if display:
                print((output / 'profile/audit.txt').read_text())
            print(f'Configuration audit: {output / "profile/audit.txt"} (unchanged; reused)')
            return
    requested = write_generated(ctx, plan, output)
    save_json(output / 'profile/build-inputs.json', inputs.provenance(ctx, plan, pins))
    save_json(owner, dict(fingerprint=plan.fingerprint))
    audit = new_audit(plan)
    save_audit(output, audit)
    try:
        run([*make_args(ctx, output), 'defconfig'])
        # Verify explicit assignments; olddefconfig can silently drop invalid choices.
        check_values(requested, read_kconfig(output / '.config'), 'Buildroot', ctx.repo, output)
        audit['checks'][0].update(status='passed', detail='Buildroot retained all requested selections.')
        audit['state'] = 'configured'
        save_audit(output, audit)
        save_json(state, dict(fingerprint=plan.fingerprint, generated=generated_hashes(output)))
        shutil.copy2(ctx.repo / 'profiles' / f'{plan.profile.name}.toml', output / 'profile/selected.toml')
    except Exception:
        state.unlink(missing_ok=True)
        audit['state'] = 'failed'
        save_audit(output, audit)
        raise
    if display:
        print(render(audit))
    print(f'Configuration audit: {output / "profile/audit.txt"}')


def build(ctx, plan, output):
    assert_current(ctx, plan, output)
    tracker = incremental.Tracker(ctx, plan.profile.name, output)
    config = read_kconfig(output / '.config')
    args = make_args(ctx, output)
    hashes, rebuilt = {}, set()
    audit = json.loads((output / 'profile/audit.json').read_text())
    audit['state'] = 'configured'
    audit['checks'] = [check for check in audit['checks'] if check['id'] != 'eeprom-preparation']
    audit['artifacts'] = []
    (output / 'profile/eeprom-result.json').unlink(missing_ok=True)
    save_audit(output, audit)
    try:
        for rule in tracker.steps:
            if rule.get('when') and config.get(rule['when']) != 'y':
                continue
            deps = {dep: hashes.get(dep) for dep in rule.get('depends_on', [])}
            fingerprint = tracker.fingerprint(rule, config, deps)
            if any(dep in rebuilt for dep in deps) or not tracker.current(rule['name'], fingerprint, rule):
                tracker.invalidate()
                for target in rule['targets']:
                    run([*args, tracker.expand(target)])
                rebuilt.add(rule['name'])
            hashes[rule['name']] = fingerprint
            tracker.remember(rule['name'], fingerprint, rule)
        for domain, pattern in (('kernel', 'linux-*/.config'), ('busybox', 'busybox-*/.config')):
            paths = list((output / 'build').glob(pattern))
            if len(paths) != 1:
                raise UserError(f'Expected one {domain} configuration in {output}')
            check_values(plan.values[domain], read_kconfig(paths[0]), domain, ctx.repo, output)
            shutil.copy2(paths[0], output / 'profile' / f'effective-{domain}.config')
        audit['checks'][1].update(status='passed', detail='All requested kernel and BusyBox settings verified.')
        # Always run final hooks/assembly: static and image checks must be current.
        tracker.invalidate()
        run([*args, 'all'])
        for check in audit['checks'][2:]:
            check.update(status='passed', detail='Buildroot post-build/image hook completed successfully.')
        key_path = output / 'profile/authorized_keys' if plan.ssh_public_key else None
        if resolve(ctx, plan.profile.name, development=plan.development,
                   ssh_public_key=key_path).fingerprint != plan.fingerprint:
            raise UserError('Inputs changed during build; result is not certified')
        assert_current(ctx, plan, output)
        tracker.commit()
        audit['state'] = 'built'
        audit['artifacts'] = artifact_manifest(output)
        if not (output / 'images/sdcard.img').is_file():
            raise UserError('No SD image produced')
        save_audit(output, audit)
        print(f'Image: {output / "images/sdcard.img"}\nAudit: {output / "profile/audit.txt"}')
    except Exception:
        tracker.invalidate()
        audit['state'] = 'failed'
        save_audit(output, audit)
        raise


def prepare_eeprom(ctx, plan, output, capture):
    if not plan.values['eeprom']:
        provision.prepare(ctx, plan.profile.name)
        return
    assert_current(ctx, plan, output, built=True)
    result = provision.prepare(ctx, plan.profile.name, capture, build_output=output, resolved=plan)
    if resolve(ctx, plan.profile.name, development=plan.development).fingerprint != plan.fingerprint:
        raise UserError('Inputs changed during EEPROM preparation; result is not certified')
    if result['bundle']:
        result['bundle'] = str(Path(result['bundle']).relative_to(output))
    save_json(output / 'profile/eeprom-result.json', result)
    audit = json.loads((output / 'profile/audit.json').read_text())
    audit['checks'] = [check for check in audit['checks'] if check['id'] != 'eeprom-preparation']
    audit['checks'].append(dict(id='eeprom-preparation', phase='artifact', status='passed',
                                detail=result['status'] + '; current board state remains unverified.'))
    audit['artifacts'] = artifact_manifest(output)
    save_audit(output, audit)


def execute(ctx, action, name, explicit=None, snapshot_name=None, *, capture=None, development=False,
            ssh_public_key=None):
    if action == 'audit' and explicit:
        path = explicit.resolve() / 'profile/audit.txt'
        if not path.is_file():
            raise UserError(f'No saved audit at {path}; configure first')
        print(path.read_text())
        return explicit.resolve()
    from .loader import load_profile
    from .resolver import profile_path
    if Group.DEVELOPMENT_ACCESS in load_profile(profile_path(ctx.repo, name)).groups:
        if action in ('configure', 'image'):
            from .development import ensure_key
            ssh_public_key = ensure_key(ctx.repo, ssh_public_key)
    plan = resolve(ctx, name, development=development, ssh_public_key=ssh_public_key)
    output = output_for(ctx, plan, explicit)
    if action == 'audit':
        path = output / 'profile/audit.txt'
        if not path.exists():
            raise UserError(f'No saved audit at {path}; configure first')
        print(path.read_text())
        return output
    if snapshot_name:
        snapshots.destination_for(ctx, snapshot_name)
    # A normal SD image is independent of board-specific EEPROM preparation.
    prepare_with_image = action == 'image' and capture is not None
    if prepare_with_image:
        capture = provision.capture_for(ctx, plan, capture)
    with incremental.locked(output):
        if action in ('configure', 'image'):
            configure(ctx, plan, output, display=action == 'configure')
        if action in ('build', 'image'):
            build(ctx, plan, output)
        if action == 'prepare-eeprom' or prepare_with_image:
            prepare_eeprom(ctx, plan, output, capture)
        if action in ('snapshot', 'image'):
            assert_current(ctx, plan, output, built=True)
            label = name + ('-development' if development else '-final')
            snapshots.snapshot(ctx, output, snapshot_name or snapshots.automatic_name(ctx, label))
    return output
