"""Command-line interface. No profile, image, or device is selected by default."""
import argparse
from pathlib import Path
import subprocess
import sys

from .artifacts import images, snapshots
from .diagnostics import smoke
from .common import Context, UserError, choose
from .profiles import workflow as profile_workflow
from .profiles.resolver import list_profiles

REPO = Path(__file__).resolve().parents[1]


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--buildroot", type=Path,
                        default=Path("/home/broosegoose/Software/buildroot-2026.08"))
    common.add_argument("--downloads", type=Path, help="download cache (default: BUILDROOT/dl)")
    common.add_argument("--snapshots", type=Path, default=REPO / ".snapshots",
                        help="snapshot directory (default: repository .snapshots/)")
    commands = result.add_subparsers(dest="command")
    commands.add_parser("profiles", help="list available TOML profiles", parents=[common])
    profile_actions = {
        'configure': 'check/prepare the host kernel logo, configure a profile, and display its audit',
        'build': 'check/prepare the host kernel logo and build the reviewed profile configuration',
        'image': 'check/prepare the host kernel logo, build and snapshot an image with first-boot EEPROM setup; expand /data during host provisioning',
        'audit': 'show the saved profile audit',
        'snapshot': 'snapshot a completed profile build',
    }
    for action, description in profile_actions.items():
        command = commands.add_parser(action, parents=[common], help=description)
        command.add_argument('--profile', help='TOML profile name')
        command.add_argument('--output', type=Path,
                             help='override the profile output directory (default: repository .output/)')
        command.add_argument('--ssh-public-key', type=Path,
                             help='development-access public key (default: create/reuse .local/development-ssh/id_ed25519)')
        if action == 'image':
            command.add_argument('--capture', type=Path, help='also prepare EEPROM changes using these saved diagnostics')
        if action in ('image', 'snapshot'):
            command.add_argument('--name', help='snapshot name (default: profile-final-YYYYMMDD, then -2, -3, etc.)')
    command = commands.add_parser('prepare-eeprom', parents=[common], help='prepare the EEPROM changes declared by a profile')
    command.add_argument('--profile', required=True, help='TOML profile name')
    command.add_argument('--capture', type=Path, help='EEPROM diagnostics (default: latest saved capture)')
    command.add_argument('--base-images', type=Path, help='normal images (default: selected profile build)')
    command.add_argument('--output', type=Path, help='override the profile output directory')
    command.add_argument('--bundle-output', type=Path, help='new standalone EEPROM bundle directory')
    for name in ("images", "flash"):
        command = commands.add_parser(name, parents=[common],
                                      help="list images" if name == "images" else "flash an SD-card image (optional --hostname NAME and --ap-name SSID)")
        if name == "images":
            command.add_argument("--output", type=Path, help="additional output directory to search")
        if name == "flash":
            command.add_argument("--image", type=Path)
            command.add_argument("--device", type=Path)
            command.add_argument("--hostname", metavar="NAME",
                                 help="set a per-card hostname in /data (requires updated startup scripts; omit .local)")
            command.add_argument("--ap-name", metavar="SSID",
                                 help="set the Wi-Fi setup AP name; 1–32 ASCII letters/digits/hyphens/underscores, starting alphanumeric; retains password")
            command.add_argument("--expand-data", action="store_true",
                                 help="grow the final polis-data ext4 partition to fill the card after flashing")
    commands.add_parser("devices", help="list disks and partitions", parents=[common])
    command = commands.add_parser("smoke", help="boot a compatible saved snapshot in QEMU (first-boot setup must be disabled)", parents=[common])
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
    return result


def main(argv=None) -> int:
    argument_parser = parser()
    args = argument_parser.parse_args(argv)
    if args.command is None:
        argument_parser.print_help()
        print("\nAvailable profiles:")
        for profile in list_profiles(REPO):
            print(f"  {profile}")
        return 0
    ctx = Context(REPO, args.buildroot.resolve(),
                  (args.downloads or args.buildroot / "dl").resolve(), args.snapshots.resolve())
    try:
        if args.command == 'profiles':
            for profile in ctx.profiles:
                print(profile)
        elif args.command in ('configure', 'build', 'image', 'audit', 'snapshot'):
            profile = args.profile or choose(ctx.profiles, 'Profile')
            if profile is not None:
                if profile not in ctx.profiles:
                    raise UserError(f"Unknown profile {profile!r}. Use 'profiles' to list choices.")
                options = dict(ssh_public_key=args.ssh_public_key)
                if args.command == 'image':
                    options['capture'] = args.capture
                profile_workflow.execute(ctx, args.command, profile, args.output, getattr(args, 'name', None), **options)
        elif args.command == 'prepare-eeprom':
            from .profiles.provision import prepare
            if args.base_images is not None or args.bundle_output is not None:
                prepare(ctx, args.profile, args.capture, args.base_images, args.bundle_output, build_output=args.output)
            else:
                profile_workflow.execute(ctx, 'prepare-eeprom', args.profile, args.output, capture=args.capture)
        elif args.command == "images":
            images.list_images(ctx, args.output)
        elif args.command == "devices":
            images.show_disks(images.disks())
        elif args.command == "flash":
            images.flash(ctx, args.image, args.device, expand_data=args.expand_data,
                         hostname=args.hostname, ap_name=args.ap_name)
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

        return 0
    except (UserError, OSError, ValueError, subprocess.CalledProcessError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    except (KeyboardInterrupt, EOFError):
        print("\nCancelled.", file=sys.stderr)
        return 130
