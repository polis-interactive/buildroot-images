#!/usr/bin/env bash
set -euo pipefail

die() { printf '%s\n' "$*" >&2; exit 1; }

list_images() {
    local root path
    local -A seen=()
    images=()
    shopt -s nullglob
    for root in "$@"; do
        for path in "$root"/images/sdcard.img "$root"/*/images/sdcard.img; do
            [[ -f "$path" ]] || continue
            path=$(realpath -- "$path")
            [[ -z "${seen[$path]:-}" ]] || continue
            seen[$path]=1
            images+=("$path")
        done
    done
    if ((${#images[@]} == 0)); then
        printf 'No sdcard.img files found. Build an image first or set IMAGE=/path/to/image.img.\n'
        return
    fi
    local i
    for i in "${!images[@]}"; do
        printf '%d) %s (%s bytes)\n' "$((i + 1))" "${images[$i]}" "$(stat -Lc %s -- "${images[$i]}")"
    done
}

list_devices() {
    lsblk --exclude 7 --paths --output NAME,TYPE,SIZE,RM,RO,TRAN,MODEL,MOUNTPOINTS
}

# Refuse mounted descendants too, including filesystems on device mapper/LVM.
validate_destination() {
    [[ -b "$device" ]] || die "Not a block device: $device"
    local type readonly mounts capacity
    type=$(lsblk -dnro TYPE -- "$device")
    [[ "$type" == disk ]] || die 'Select a whole disk, not a partition or mapped device.'
    readonly=$(lsblk -dnro RO -- "$device")
    [[ "$readonly" == 0 ]] || die 'The destination is read-only.'
    mounts=$(lsblk -nrpo MOUNTPOINTS -- "$device")
    [[ ! "$mounts" =~ [^[:space:]] ]] || die 'The disk has mounted filesystems or active swap. Unmount them first.'
    capacity=$(lsblk -bdnro SIZE -- "$device")
    [[ "$capacity" =~ ^[0-9]+$ ]] || die 'Could not determine disk capacity.'
    ((capacity >= image_size)) || die 'The image is larger than the destination disk.'
}

flash() {
    local image=${IMAGE:-} device=${DEVICE:-} choice confirmation image_size
    list_images "$@"
    [[ -t 0 ]] || die 'Flashing requires an interactive terminal for confirmation.'
    if [[ -z "$image" ]]; then
        ((${#images[@]} > 0)) || die 'No image available to flash.'
        read -r -p 'Image number: ' choice
        [[ "$choice" =~ ^[1-9][0-9]{0,5}$ ]] || die 'Invalid image number.'
        ((choice <= ${#images[@]})) || die 'Invalid image number.'
        image=${images[$((choice - 1))]}
    fi
    image=$(realpath -e -- "$image")
    [[ -f "$image" && -r "$image" ]] || die "Not a readable image file: $image"
    image_size=$(stat -Lc %s -- "$image")
    ((image_size > 0)) || die 'The image is empty.'

    list_devices
    if [[ -z "$device" ]]; then
        read -r -p 'Destination whole disk (for example /dev/sdb): ' device
    fi
    device=$(realpath -e -- "$device")
    validate_destination

    printf '\nImage: %s\nDestination: %s\n' "$image" "$device"
    lsblk -dnpo NAME,SIZE,MODEL,SERIAL -- "$device"
    printf 'All existing data on this disk will be overwritten.\n'
    read -r -p "Type $device to confirm (anything else cancels): " confirmation
    [[ "$confirmation" == "$device" ]] || die 'Cancelled; no data written.'

    local -a privilege=()
    if ((EUID != 0)); then
        sudo -v
        privilege=(sudo)
    fi
    # Recheck after the confirmation and possible sudo password prompt.
    validate_destination
    "${privilege[@]}" dd if="$image" of="$device" bs=4M conv=fsync status=progress
    printf 'Image written and flushed to %s.\n' "$device"
}

main() {
    local action=${1:-}
    (($# > 0)) && shift
    case "$action" in
        list-images) list_images "$@" ;;
        list-devices) list_devices ;;
        flash) flash "$@" ;;
        *) die 'Usage: flash-sd.sh {list-images|list-devices|flash} [image-search-directory ...]' ;;
    esac
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    main "$@"
fi
