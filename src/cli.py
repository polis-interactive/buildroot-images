"""Command-line interface. No board, image, or device is selected by default."""
import argparse
from pathlib import Path
import subprocess
import sys

from . import builds, images, snapshots, smoke
from .common import Context, UserError, choose

REPO = Path(__file__).resolve().parents[1]


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--buildroot", type=Path,
                        default=Path("/home/broosegoose/Software/buildroot-2026.08"))
    common.add_argument("--downloads", type=Path, help="download cache (default: BUILDROOT/dl)")
    common.add_argument("--snapshots", type=Path, default=REPO / "snapshots")
    commands = result.add_subparsers(dest="command")
    commands.add_parser("boards", help="list board configurations", parents=[common])
    for action in builds.ACTIONS:
        command = commands.add_parser(action, parents=[common],
                                      help="configure, build, and snapshot a board" if action == "build"
                                      else f"run Buildroot {action} for an explicitly selected board")
        command.add_argument("--board", help="defconfig name without _defconfig")
        command.add_argument("--output", type=Path, help="override output/<board>")
        if action == "build":
            command.add_argument("--name", help="snapshot name (default: board-YYYYMMDD, then -2, -3, etc.)")
            command.add_argument("--no-configure", action="store_true",
                                 help="use the existing .config, preserving unsaved menuconfig changes")
    for name in ("images", "flash"):
        command = commands.add_parser(name, parents=[common],
                                      help="list images" if name == "images" else "select and flash an SD-card image")
        command.add_argument("--output", type=Path, help="additional output directory to search")
        if name == "flash":
            command.add_argument("--image", type=Path)
            command.add_argument("--device", type=Path)
            command.add_argument("--expand-data", action="store_true",
                                 help="grow the final polis-data ext4 partition to fill the card after flashing")
    commands.add_parser("devices", help="list disks and partitions", parents=[common])
    command = commands.add_parser("smoke", help="select a completed snapshot and boot it in QEMU", parents=[common])
    command.add_argument("--snapshot", help="name of a completed capture under --snapshots")
    command.add_argument("--runs", type=int, help="boot count (prompt when omitted; default: 3)")
    command.add_argument("--timeout", type=float, default=120, help="seconds per boot (default: 120)")
    readiness = command.add_mutually_exclusive_group()
    readiness.add_argument("--target", choices=("init", "storage", "services"),
                           help="readiness target (prompt when omitted): init scripts finish, "
                           "storage checks pass, or storage and D-Bus/iwd checks pass")
    readiness.add_argument("--expect", help="custom readiness text instead of --target; "
                           "login prompts need no newline")
    command.add_argument("--results", type=Path, help="new results directory (default: .cache/smoke/<timestamp>)")
    command.add_argument("--qemu", help="qemu-system-aarch64 executable")
    command.add_argument("--fdtput", help="fdtput executable")
    command = commands.add_parser("snapshot", help="capture a completed build without overwriting a name", parents=[common])
    command.add_argument("--board", help="defconfig name without _defconfig")
    command.add_argument("--output", type=Path, help="override selected board output directory")
    command.add_argument("--name", help="snapshot name (default: board-YYYYMMDD, then -2, -3, etc.)")
    return result


def main(argv=None) -> int:
    argument_parser = parser()
    args = argument_parser.parse_args(argv)
    if args.command is None:
        argument_parser.print_help()
        print("\nAvailable boards:")
        for board in sorted((REPO / "configs").glob("*_defconfig")):
            print(f"  {board.name.removesuffix('_defconfig')}")
        return 0
    ctx = Context(REPO, args.buildroot.resolve(),
                  (args.downloads or args.buildroot / "dl").resolve(), args.snapshots.resolve())
    try:
        if args.command == "boards":
            for board in ctx.boards:
                print(board)
        elif args.command in builds.ACTIONS:
            board = args.board or choose(ctx.boards, "Board")
            if board is not None:
                if args.command == "build":
                    builds.build(ctx, args.command, board, args.output,
                                 name=args.name, configure=not args.no_configure)
                else:
                    builds.build(ctx, args.command, board, args.output)
        elif args.command == "images":
            images.list_images(ctx, args.output)
        elif args.command == "devices":
            images.show_disks(images.disks())
        elif args.command == "flash":
            images.flash(ctx, args.image, args.device, args.output, expand_data=args.expand_data)
        elif args.command == "smoke":
            name = args.snapshot or choose(snapshots.completed(ctx), "Snapshot")
            if name is None:
                return 0
            source = snapshots.source_for(ctx, name)
            expect = args.expect
            if expect is None:
                target = args.target or choose(["init", "storage", "services"], "Target")
                if target is None:
                    return 0
                expect = f"POLIS_SMOKE_READY {target}"
            runs = args.runs
            if runs is None:
                answer = input("Number of runs [3]: ").strip() if sys.stdin.isatty() else ""
                if answer and not answer.isdecimal():
                    raise UserError("Number of runs must be a positive whole number.")
                runs = int(answer) if answer else 3
            if runs < 1:
                raise UserError("Number of runs must be a positive whole number.")
            smoke.smoke(ctx, source, runs=runs, timeout=args.timeout, expect=expect,
                        results=args.results, qemu=args.qemu, fdtput=args.fdtput)
        elif args.command == "snapshot":
            board = args.board or choose(ctx.boards, "Board")
            if board is None:
                return 0
            # Apply the same configuration ownership check as a build, without running Make.
            builds.build_command(ctx, "build", board, args.output)
            source = ctx.output(board, args.output)
            name = args.name if args.name is not None else snapshots.automatic_name(ctx, board)
            snapshots.snapshot(ctx, source, name)
        return 0
    except (UserError, OSError, ValueError, subprocess.CalledProcessError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    except (KeyboardInterrupt, EOFError):
        print("\nCancelled.", file=sys.stderr)
        return 130
