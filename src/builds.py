"""Buildroot commands; the upstream build engine remains GNU Make."""
from pathlib import Path
import shlex

from . import incremental, snapshots
from .common import Context, UserError, read_config, run

ACTIONS = {
    "configure": None,
    "build": "all",
    "menuconfig": "menuconfig",
    "savedefconfig": "savedefconfig",
    "linux-reconfigure": "linux-reconfigure",
    "busybox-reconfigure": "busybox-reconfigure",
}

def build_command(ctx: Context, action: str, board: str,
                  output: Path | None = None) -> list[str]:
    ctx.require_board(board)
    out = ctx.output(board, output)
    defconfig = ctx.repo / "configs" / f"{board}_defconfig"
    config = out / ".config"
    if config.exists():
        saved = read_config(config)
        if saved.get("BR2_DEFCONFIG") != str(defconfig):
            raise UserError(f"{out} belongs to another configuration. Choose a fresh --output.")
    elif action != "configure":
        raise UserError(f"{out} is not configured. Run configure --board {board} first.")
    args = ["make", "-C", str(ctx.buildroot), f"O={out}",
            f"BR2_EXTERNAL={ctx.repo}", f"BR2_DL_DIR={ctx.downloads}",
            f"{board}_defconfig" if action == "configure" else ACTIONS[action]]
    if action == "savedefconfig":
        args.append(f"BR2_DEFCONFIG={defconfig}")
    return args


def build(ctx: Context, action: str, board: str, output: Path | None,
          *, name: str | None = None, configure: bool = True) -> None:
    # Validate ownership before creating a cache or taking the output lock.
    args = build_command(ctx, "configure" if action == "build" and configure else action,
                         board, output)
    if action == "build" and name is not None:
        snapshots.destination_for(ctx, name)
    out = ctx.output(board, output)
    with incremental.locked(out):
        # A competing wrapper may have configured this directory while waiting.
        build_command(ctx, "configure" if action == "build" and configure else action,
                      board, output)
        if action != "build":
            (out / incremental.STATE).unlink(missing_ok=True)
            print(shlex.join(args), flush=True)
            run(args)
            return
        tracker = incremental.Tracker(ctx, board, out)

        def execute(label, rule):
            tracker.invalidate()
            print(f"Building {label}: inputs changed or outputs missing/changed", flush=True)
            # Separate invocations preserve ordering even with MAKEFLAGS=-j.
            for target in rule['targets']:
                command = [*args[:-1], tracker.expand(target)]
                print(shlex.join(command), flush=True)
                run(command)

        configure_rule = dict(tracker.policy['configure'])
        configure_rule['inputs'] = [*configure_rule.get('inputs', []),
                                     str(tracker.manifest),
                                     *tracker.policy.get('includes', [])]
        config_hash = tracker.fingerprint(configure_rule, {})
        configured = tracker.current('configure', config_hash, configure_rule)
        if configure and not configured:
            execute('configure', configure_rule)
        elif configure:
            print('Skipping configure: inputs and outputs match', flush=True)
        if configure or configured:
            tracker.remember('configure', config_hash, configure_rule)

        config = read_config(out / '.config')
        hashes, changed = {}, set()
        active = [rule for rule in tracker.steps
                  if not rule.get('when') or config.get(rule['when']) == 'y']
        for rule in active:
            label = rule['name']
            deps = {dep: hashes.get(dep) for dep in rule.get('depends_on', [])}
            fingerprint = tracker.fingerprint(rule, config, deps)
            if (any(dep in changed for dep in deps)
                    or not tracker.current(label, fingerprint, rule)):
                execute(label, rule)
                changed.add(label)
            else:
                print(f'Skipping {label}: inputs and outputs match', flush=True)
            hashes[label] = fingerprint
            tracker.remember(label, fingerprint, rule)

        image = tracker.policy['image']
        image_hash = tracker.fingerprint(image, config, hashes)
        if changed or not tracker.current('image', image_hash, image):
            execute('image', image)
        else:
            print('Skipping image: inputs and outputs match', flush=True)
        tracker.remember('image', image_hash, image)

        # Never certify artifacts against inputs edited while the build ran.
        stable = tracker.fingerprint(configure_rule, {}) == config_hash
        stable &= read_config(out / '.config') == config
        for rule in active:
            deps = {dep: hashes.get(dep) for dep in rule.get('depends_on', [])}
            stable &= tracker.fingerprint(rule, config, deps) == hashes[rule['name']]
        stable &= tracker.fingerprint(image, config, hashes) == image_hash
        if not stable:
            tracker.invalidate()
            raise UserError('Build inputs changed during the build; run make build again.')
        tracker.commit()
        print("Build ready; saving snapshot...", flush=True)
        snapshots.snapshot(ctx, out, name if name is not None else snapshots.automatic_name(ctx, board))
