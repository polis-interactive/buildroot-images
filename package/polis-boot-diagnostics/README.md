# Boot diagnostics collector

The `boot-diagnostics` profile group collects one immutable capture per Linux
boot in `/data/boot-diagnostics/<boot-id>/`. The service starts in the background,
waits for writable ext4 `/data`, saves and flushes its capture, then exits.
It does not open a UART, drive GPIO pins, or blink LEDs.

`app/` contains the Rust collector, which uses only the standard library.
`boot-diagnostics-tools/` builds the pinned Raspberry Pi `vclog` and `vcgencmd`
utilities. Captures include `boot.log`, kernel logs, firmware logs/configuration,
command line, mount information, uptime and kernel version. Failed firmware
queries produce error files without discarding the remaining capture.
Publication is atomic; restarting the collector preserves the first completed
capture for that boot.

The `usb-diagnostics` service transfers these files to the host. It waits for
`boot.log`; `src/wired/capture.py` validates and archives the transfer for
`polis-wired-control`. The host also accepts `gpio.log` from older installed
images and historical captures. New captures contain no GPIO timing, so the old
GPIO readiness metrics are unavailable rather than repurposed as collector timing.

`install.py` owns the init integration; `build.toml` tracks the collector and its
C utilities. The static-core policy verifies all three executables.
Host overlay merging remains in `../polis-dtmerge/`.
