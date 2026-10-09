"""Buildroot commands; the upstream build engine remains GNU Make."""
from pathlib import Path
import shlex

from . import snapshots
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
    if action == "build":
        ctx.require_board(board)
        # Reject invalid or reused names before spending time on a build.
        if name is not None:
            snapshots.destination_for(ctx, name)
        if configure:
            build(ctx, "configure", board, output)
    args = build_command(ctx, action, board, output)
    print(shlex.join(args), flush=True)
    run(args)
    if action == "build":
        print("Build completed; saving snapshot...", flush=True)
        snapshots.snapshot(ctx, ctx.output(board, output),
                           name if name is not None else snapshots.automatic_name(ctx, board))
