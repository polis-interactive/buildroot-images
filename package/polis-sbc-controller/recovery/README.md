# Reproducible RAM recovery payload

`make setup` prepares `local/polis-sbc-controller-tools/gadget/` using:

- `config.txt` from this directory;
- `boot.img` and dereferenced `bootfiles.bin` from the pinned usbboot checkout's
  `mass-storage-gadget64/` directory;
- `payload.sha256`, the commissioned directory hash, shared by setup and the daemon.

The recipe is `src/setup/recovery.py`; usbboot download pins are in
`src/setup/usbboot.py`. Setup checks the entire payload hash and rejects
EEPROM/OTP provisioning payloads before publishing the directory. Existing
modified payloads are rejected and preserved. Setup never operates hardware.

The downloaded binaries remain outside Git. `make purge` deletes both
`local/usbboot/` and `local/polis-sbc-controller-tools/`; `make setup` recreates them.
Only purge while recovery tools are idle.

The old `local/polis-recovery/` directory is historical commissioning evidence,
including a superseded payload copy and a one-time provisioning script. No
current runtime or setup command reads it. The provisioning script is not a
supported tool and must not be replayed as setup. Keep evidence you need;
there is no unique runtime source in that directory anymore.

`local/polis-sbc-controller-state/` contains live sockets, UART logs and operation
journals. Those remain local state, not package source, and are not purged.
The separately pinned known-good SD image remains a snapshot; setup does not
recreate or select a replacement image.
