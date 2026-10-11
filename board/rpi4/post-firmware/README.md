# Pi 4 EEPROM preparation

The profile's `[eeprom]` assignments determine preparation. `make image` runs
this stage after assembling the normal SD image and before saving its snapshot.
It can also run separately:

```sh
make prepare-eeprom ARGS="--profile polis-rpi4-base"
make prepare-eeprom ARGS="--profile polis-rpi4-speed"
```

Base reports nothing to do. Speed selects `NET_INSTALL_ENABLED=0`, disabling
bootloader keyboard/network-installer detection. The board backend supports
that setting on the commissioned `bench-rpi4b` with firmware release
`57db150d63864d47e6c9071f6b086a5401eb4e92` and its known configuration.
Unfamiliar firmware/configurations fail explicitly.

Preparation uses the profile's built images and the latest imported diagnostics
under `local/polis-wired-state/captures`. Capture file hashes are checked. Supply
`--capture DIRECTORY` on `image` or `prepare-eeprom` to choose a saved capture.
Use `make wired-diagnostics` to collect one from the running commissioned fixture.
Preparation itself never accesses hardware.

The standalone command can use explicit paths:

```sh
make prepare-eeprom ARGS="--profile polis-rpi4-speed --capture CAPTURE --base-images SNAPSHOT/images --bundle-output NEW_DIRECTORY"
```

Without those overrides, preparation is recorded in the build audit and selected
bundle under `output/<profile>-<hash>/eeprom/`. The automatic snapshot includes
that bundle and its captured evidence. If the saved configuration already
matches, it records `matches-saved-capture` and creates no update image. This is
not a claim about the board's current state.

When changes are required, `manifest.json` records the original/requested
settings, firmware hash, image hashes and timestamp. `before.conf` and
`rollback.bin` retain the original configuration and reconstructed same-release
firmware, not a physical EEPROM dump. The backend checks executable firmware
sections and permits only updater files to change on the temporary boot FAT.
The normal SD image is unchanged. The pinned tools live in
`local/usbboot/rpi-eeprom`, downloaded by `make setup`; `mcopy` comes from the
profile's host tools or PATH. Rerun `make setup` if the tool checkout was removed.

## Apply a prepared update

Review the bundle, then explicitly apply it from its writable build directory:

```sh
python3 board/rpi4/post-firmware/apply.py OUTPUT/eeprom/BUNDLE \
  --confirm-eeprom-write bench-rpi4b
```

The script validates image hashes and live EEPROM configuration before asking
the daemon to flash/read back the temporary update SD image. The bootloader
applies the update and resets. The script verifies the captured version and
configuration, restores the clean SD image, then verifies again.

`apply.json` journals each operation; an existing journal prevents replay. After
an interruption, inspect the recorded run IDs and daemon status. A daemon
recovery attempt causes the script to stop for review. This does not perform
automatic EEPROM rollback; GPIO-forced ROM USB recovery remains the recovery
route for an interrupted update.

Snapshots are read-only. To apply from a snapshot, copy it to a writable working
directory, retaining its `images/` and `eeprom/` layout. Manifest paths are relative
so the prepared and normal image references remain valid after copying.

To restore original settings, prepare a new directory using a current capture
and `prepare.py --restore-config PREVIOUS_BUNDLE/before.conf`, along with
`--capture`, `--base-images` and `--output`. Apply that newly prepared bundle.
Restoration requires a fresh timestamp; do not blindly reuse an old signature.

An SD-image restore does not restore EEPROM state. Deselecting the optimization
also does not undo a setting installed previously. Record EEPROM settings with
boot measurements.

Software-only guard checks:
`python3 -B board/rpi4/post-firmware/test_steps.py`.
Historical measurements remain in the
[pre-Linux runlog](../../../package/polis-wired-control/docs/prelinux-runlog.md).
