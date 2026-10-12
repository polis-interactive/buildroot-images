"""Prepare profile EEPROM artifacts from saved diagnostics; never access hardware."""
import json
from pathlib import Path
import runpy
import shutil
import subprocess
from uuid import uuid4

from ..common import UserError, run
from .resolver import file_hash, resolve

CAPTURE_FILES = ('bootloader-config.txt', 'bootloader-version.txt')


def capture_for(ctx, plan, explicit=None):
    if not plan.values['eeprom']:
        return None
    capture = explicit
    if capture is None:
        candidates = []
        for metadata in (ctx.repo / '.local/polis-sbc-controller-state/captures').glob('*/capture.json'):
            data = json.loads(metadata.read_text())
            candidates.append((data.get('imported_utc', ''), metadata.parent))
        if candidates:
            capture = max(candidates)[1]
    if capture is None:
        raise UserError('EEPROM preparation needs saved diagnostics: run make sbc-controller-diagnostics '
                        'or supply --capture CAPTURE_DIRECTORY')
    capture = capture.resolve()
    metadata = capture / 'capture.json'
    if not metadata.is_file():
        raise UserError(f'{capture}: missing capture.json with verified diagnostic hashes')
    hashes = json.loads(metadata.read_text()).get('sha256', {})
    for name in CAPTURE_FILES:
        path = capture / name
        if not path.is_file() or hashes.get(name) != file_hash(path):
            raise UserError(f'{path}: missing or changed EEPROM capture; collect diagnostics again')
    return capture


def prepare(ctx, name, capture: Path | None = None, images: Path | None = None,
            output: Path | None = None, *, build_output: Path | None = None, resolved=None):
    # Local import keeps workflow orchestration separate from this preparation backend.
    from .workflow import output_for
    plan = resolved or resolve(ctx, name)
    settings = plan.values['eeprom']
    if not settings:
        print(f'{name}: nothing to do; no EEPROM changes selected.')
        return dict(status='not-requested', settings={}, bundle=None)
    capture = capture_for(ctx, plan, capture)
    build_output = output_for(ctx, plan, build_output)
    images = (images or build_output / 'images').resolve()
    if not (images / 'sdcard.img').is_file():
        raise UserError(f'{images}: build the profile before preparing EEPROM artifacts')
    output = (output or build_output / 'eeprom' / uuid4().hex).resolve()
    if output.exists():
        raise UserError(f'{output}: EEPROM preparation requires a new directory')
    backend = ctx.repo / 'board/rpi4/post-firmware/prepare.py'
    # Use exactly the same board/version/configuration checks for no-op and update.
    implementation = runpy.run_path(str(backend))
    validate = implementation['configuration']
    try:
        before, after, _ = validate(capture, [f'{k}={v}' for k, v in sorted(settings.items())])
    except SystemExit as error:
        raise UserError(str(error)) from error
    matches = implementation['active'](before) == implementation['active'](after)
    if matches:
        output.mkdir(parents=True)
        print(f'{name}: requested EEPROM settings already match the saved capture; no update needed.')
    else:
        args = ['python3', str(backend), '--capture', str(capture),
                '--base-images', str(images), '--output', str(output)]
        for key, value in sorted(settings.items()):
            args += ['--set', f'{key}={value}']
        mcopy = build_output / 'host/bin/mcopy'
        if mcopy.is_file():
            args += ['--mcopy', str(mcopy)]
        try:
            run(args, capture=True)
        except subprocess.CalledProcessError as error:
            raise UserError((error.stderr or '').strip() or str(error)) from error
        print(f'Prepared EEPROM update and rollback for {name}: {output}')
    evidence = output / 'capture'
    evidence.mkdir()
    for filename in (*CAPTURE_FILES, 'capture.json'):
        shutil.copy2(capture / filename, evidence / filename)
    # Detect a capture changed while the backend or copies ran.
    capture_for(ctx, plan, evidence)
    result = dict(schema=1, profile=name, settings=settings,
                  status='matches-saved-capture' if matches else 'prepared',
                  bundle=str(output), source_capture=str(capture),
                  note='Preparation does not verify current board state or apply EEPROM changes.')
    (output / 'preparation.json').write_text(json.dumps(result, indent=2) + '\n')
    return result


def bundle_for(output):
    """Return only the selected, complete bundle, keeping snapshots self-contained."""
    record = output / 'profile/eeprom-result.json'
    if not record.is_file():
        return None
    result = json.loads(record.read_text())
    if result['bundle'] is None:
        return None
    relative = Path(result['bundle'])
    if relative.is_absolute() or '..' in relative.parts or relative.parts[:1] != ('eeprom',):
        raise UserError(f'{record}: invalid EEPROM bundle path')
    bundle = output / relative
    if not (bundle / 'preparation.json').is_file():
        raise UserError(f'{bundle}: incomplete EEPROM preparation')
    return bundle
