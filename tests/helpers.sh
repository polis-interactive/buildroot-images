#!/usr/bin/env bash
# Read-only checks and temporary fixtures; never writes a block device.
set -euo pipefail
repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
source "$repo/scripts/flash-sd.sh"
fixture=$(mktemp -d)
trap 'rm -rf -- "$fixture"' EXIT

mkdir -p "$fixture/output/board one/images" "$fixture/output/board-two/images"
printf 'first image' > "$fixture/output/board one/images/sdcard.img"
printf 'second image' > "$fixture/output/board-two/images/sdcard.img"
list_images "$fixture/output" "$fixture/output/board one" > "$fixture/list"
[[ ${#images[@]} == 2 ]]
[[ ${images[0]} == "$fixture/output/board one/images/sdcard.img" ]]
[[ ${images[1]} == "$fixture/output/board-two/images/sdcard.img" ]]
list_images "$fixture/missing" > "$fixture/list"
[[ ${#images[@]} == 0 ]]
grep -q 'No sdcard.img' "$fixture/list"

# A flash invocation without a terminal must never reach the writer.
dd() { touch "$fixture/writer-called"; return 99; }
export IMAGE="$fixture/output/board one/images/sdcard.img"
export DEVICE="$fixture/not-a-device"
if (flash "$fixture/output" </dev/null) > "$fixture/log" 2>&1; then
    die 'Expected noninteractive flashing to fail.'
fi
grep -q 'interactive terminal' "$fixture/log"
[[ ! -e "$fixture/writer-called" ]]

device="$IMAGE"
image_size=1
if (validate_destination) > "$fixture/log" 2>&1; then
    die 'Expected a regular file destination to be refused.'
fi
grep -q 'Not a block device' "$fixture/log"

# Exercise board discovery/routing using a copy and an echo-only build command.
mkdir -p "$fixture/project/configs"
cp "$repo/Makefile" "$fixture/project/Makefile"
touch "$fixture/project/configs/polis_rpi4_defconfig" "$fixture/project/configs/other_defconfig"
make -s -C "$fixture/project" list-boards > "$fixture/list"
grep -qx 'polis_rpi4 (selected)' "$fixture/list"
grep -qx 'other' "$fixture/list"
make -s -C "$fixture/project" BOARD=other MAKE=echo configure > "$fixture/log"
grep -q 'other_defconfig' "$fixture/log"
grep -q "O=$fixture/project/output/other" "$fixture/log"
make -s -C "$fixture/project" MAKE=echo configure > "$fixture/log"
grep -q "O=$fixture/project/output/rpi4" "$fixture/log"
if make -s -C "$fixture/project" BOARD=missing MAKE=echo build > "$fixture/log" 2>&1; then
    die 'Expected an unknown board to fail.'
fi
grep -q 'Unknown BOARD' "$fixture/log"
mkdir -p "$fixture/project/output/other"
printf 'BR2_DEFCONFIG="wrong-board"\n' > "$fixture/project/output/other/.config"
if make -s -C "$fixture/project" BOARD=other MAKE=echo savedefconfig > "$fixture/log" 2>&1; then
    die 'Expected a mismatched output configuration to fail.'
fi
grep -q 'does not match BOARD' "$fixture/log"
printf 'PASS: image discovery, flash refusal checks, and board selection.\n'
