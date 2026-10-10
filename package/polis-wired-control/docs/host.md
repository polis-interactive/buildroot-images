# Host daemon, CLI, and MCP

Linux host only. The firmware, daemon, and frontend are the three applications;
`host-api` is their shared local API library. The daemon connects to the Pico
and owns recovery USB operations. CLI/MCP clients never open serial ports or
block devices. Recovery prerequisites and wiring are in [recovery.md](recovery.md).

## Fixture constants

Run `make wired-daemon` from the repository root. No configuration file is read
or generated. All host fixture values are at the top of
[daemon/src/settings.rs](../daemon/src/settings.rs); firmware pin assignments
remain at the top of [firmware/src/board.rs](../firmware/src/board.rs).
The make target rebuilds the daemon when these constants change.

The Pi now draws dedicated 5 V power through the Pico-controlled MOSFET circuit.
Cold recovery boot and a 30-second four-core load check reported no voltage or
throttling flags; earlier laptop-fed power did report undervoltage. See the
[bench wiring and evidence](recovery.md#current-bench-wiring-and-evidence--2026-10-09).

The connected Pico is pinned by chip ID and its stable `/dev/serial/by-id/`
path. Startup verifies that ID through Hello. Power/UART bring-up requires no
Pi recovery USB connection, OTP provisioning, recovery payload, or image.
The bench constants now set `RECOVERY_ENABLED=true`, with the actual Pi USB
port, SD LUN identity, RAM payload hash, and known-good image pinned. See
[commissioning progress](recovery.md#commissioning-progress--2026-10-09) for
hardware acceptance evidence. Setting it false refuses flash and automatic
recovery requests before issuing a power command. Normal power, UART, and status
remain available. Startup itself never powers on or flashes the Pi.

For initial hardware commissioning, `recovery-usb --confirm-fixture bench-rpi4b`
(MCP `recovery_usb`) cold-boots recovery and loads the pinned RAM gadget without
writing storage. It requires qualified GPIO8 OTP, a pinned USB port/payload, and
host USB permissions, but permits reflashing to remain disabled until the actual
SD LUN identities are recorded. Poll `status` using the returned run ID. A lost
reply or failed run must be inspected before another explicit invocation.

`local/polis-wired-state/` stores logs and run journals only. Its private socket
is `daemon.sock`; it is not a configuration directory. The repository's absolute
path is embedded at build time. Rebuild after moving the checkout.

To enable Pi reflashing later, fill in the recovery constants and explicitly set
`RECOVERY_ENABLED` to true. `RECOVERY_IMAGE` is an optional `(repository-relative
or absolute path, SHA-256)` pair for the known-good image; relative paths are
resolved before constructing the host API configuration. Keep `OTP_GPIO8_PROVISIONED` false until
separately authorized hardware qualification is complete. The existing
[recovery prerequisites](recovery.md) still apply; no disk or image is guessed.

Keep the Pico on its independently powered USB connection. Commission the Pi
recovery USB topology (`rpiboot -p`, e.g. `1-2.3`) and **both** the intended SD
LUN's udev `ID_PATH` and `ID_SERIAL_SHORT`. These are distinct from the Pico ID.
They must survive re-enumeration for this fixture. Do not copy example identities
or choose a `/dev/sdX` by position. If the gadget release lacks a stable serial,
qualify a payload that provides one before using this workflow; do not relax the
selector. A changed host hub/port mapping requires explicit recommissioning.

Set a readiness marker that the normal image actually writes to UART. Rebuilt
`polis_*` images emit the default `POLIS_SMOKE_READY init`. Successful
GPIO power-on, USB recovery enumeration, and a recovery-kernel login prompt do
not establish normal-image readiness. The daemon filters old/recovery UART events
using a fresh host cursor and a Pico timestamp captured while the Pi is off.

Images are raw, uncompressed, sector-aligned files selected by absolute path and
SHA-256. `RECOVERY_IMAGE` may be `None` to disable automatic recovery. To enable
recovery, provision/qualify GPIO8 separately and set the qualification flag only
afterward. Pin an official RAM gadget payload containing `config.txt`, `boot.img`,
and either `bootfiles.bin` with `2711/bootcode4.bin` or a loose `bootcode4.bin`;
EEPROM/OTP recovery payloads are rejected. The prepared payload and commissioning
status are recorded in [recovery.md](recovery.md#commissioning-progress--2026-10-09).
Hashes can be computed without device access:

```sh
sha256sum /absolute/path/to/known-good/sdcard.img
# From the BuildrootImages repository root:
python3 -m src.wired_storage payload-hash /opt/polis-recovery/mass-storage-gadget64
```

The directory hash includes each relative filename and the SHA-256 of its bytes,
in sorted order. It detects payload changes; it is not a substitute for obtaining
a trusted official payload and qualifying its exported microSD LUN.

The daemon needs permission to open the Pico CDC device, run rpiboot/libusb,
open the selected block device exclusively. Readback uses page-aligned Linux
`O_DIRECT` reads after `fsync`, bypassing the host page cache without requiring
root-only `BLKFLSBUF`. Direct-I/O support and sector alignment are checked before
the first write; failed direct reads never fall back to buffered verification.
See the Linux [O_DIRECT alignment documentation](https://man7.org/linux/man-pages/man2/open.2.html#NOTES).
Supply device permissions through the commissioned udev rules. The daemon
never invokes sudo or changes permissions on
physical devices. Host tools: Python 3, `lsblk`, `udevadm`, and `rpiboot` with `-p`
support. Disable automount for the commissioned recovery gadget; mounted media,
swap, and active device-mapper/RAID holders are refused, never auto-unmounted.
The Python helper must run from this repository so it can reuse `src.images`.

## Commands after separately authorized bring-up

From the repository root, build and start the daemon in the foreground:

```sh
make wired-daemon
```

Ctrl-C stops the daemon. This target does not launch a background service or start a power/flash
workflow. Use a second terminal for CLI/MCP commands.

Build from `package/polis-wired-control/` with `cargo build --locked`. Starting
the daemon opens the Pico and sends Hello/Status traffic, but starts no power
operation or flashing workflow. It sends read-only Status keepalives every second,
including while a long cycle is pending, to maintain daemon presence.
Four seconds without accepted requests makes the onboard LED flash, switches Pi
power off, cancels pending cycles/Boot, and aborts UART transmission. USB reset
or disconnection triggers this shutdown on the next controller pass. Reconnection
leaves power off until explicitly commanded on; commands must not be replayed. Equivalent direct invocation and client examples:

```sh
target/debug/polis-wired-daemon

target/debug/polis-wired --socket /private/state/daemon.sock --fixture bench-rpi4b status
target/debug/polis-wired --socket /private/state/daemon.sock --fixture bench-rpi4b images
```

A run without `--image` performs a normal power cycle and readiness observation.
An explicit image first enters recovery, loads the RAM gadget, writes and
verifies that image, and returns to normal boot:

```sh
target/debug/polis-wired --socket /private/state/daemon.sock --fixture bench-rpi4b \
  run --image /absolute/path/candidate.img --sha256 IMAGE_SHA256 \
  --confirm-fixture bench-rpi4b
```

Adding `--auto-recover` explicitly authorizes **one** flash of the configured
known-good image if normal-boot readiness times out. It does not retry storage,
loader, transport, or ambiguous operation failures. The initial candidate flash
is separate from that one recovery attempt. A second readiness failure stops.
A run returns its ID promptly; use `status` to observe the current stage/result.
`active: false` with `stage: ready` or `recovered` indicates success; `failed`
or `interrupted` requires inspection. No action is automatically resumed after
a daemon restart or a lost client response.

Other commands are `power on|off --confirm-fixture NAME`,
`cycle --off-ms 1000 --confirm-fixture NAME`,
`console-read --after OFFSET --max-bytes 4096`, and `console-write TEXT`.
Console read returns the next cursor and a gap indicator. Writes are at most
128 UTF-8 bytes; these are raw UART bytes, not a shell-execution protocol.
Power, cycle, console-write, and another run are refused while a workflow holds
the fixture. Status and captured console reads remain available.

## First Pi UART connectivity check

Rebuild and flash the base `polis_*` image through the existing SD workflow.
The image now includes 115200-baud UART kernel logs, the init readiness marker,
and the direct Pi USB serial diagnostic endpoint. **Do not edit the flashed
card's boot arguments:** these defaults are included in every new build.
Older snapshots retain their old behavior and need replacing with the new image.

1. Start `make wired-daemon` before powering on the Pi.
2. Expect kernel logs followed by `POLIS_SMOKE_READY init uptime=...` on the
   daemon terminal. The source `READINESS_MARKER` now matches that output.
3. Run `make wired-usb-test` to test laptop -> Pi -> laptop USB data exchange.
   It selects the Pi by product `Polis_Pi_Diagnostics`, independently of the
   Pico's `Polis Wired Control` connection, and refuses ambiguous selection.
   The host retries only the harmless ping every 250 ms within a five-second
   deadline: USB can enumerate before the Pi responder opens its serial port.
   A disconnect ends the test; no power or flash command is replayed.

The Pi USB service configures raw input without requesting a hardware baud
rate: Linux g_serial does not implement baud changes on `/dev/ttyGS0`.
Requesting 115200 there causes BusyBox stty to fail and prevents the service
from starting. Its startup errors go to the UART console. This does not change
the separate physical UART, which remains at 115200 baud.

The UART marker establishes Pi-to-Pico traffic and init completion, not the
reverse UART direction or a shell. The USB ping executes no commands and does
not require rcS success. Neither check establishes power stability under load,
OTP provisioning, or the separate ROM/RAM recovery route. A missing USB device
can mean failure before gadget registration or a power/data wiring problem.

The daemon terminal now prints readable Pi UART lines as well as connection
messages. `POLIS_*` lines are tagged `MARKER`; lines mentioning errors, failures,
panics, or undervoltage are tagged `WARN`. This is a display classification, not
proof that the configured readiness condition passed. To watch the original bytes
from a second terminal, use `tail -f local/polis-wired-state/uart.bin`. An empty file and
`uart_rx_offset: 0` mean no bytes were captured; a successful Pico Hello proves
only the host-to-Pico USB connection. Use the exact spelling `POLIS_SMOKE_READY`.

## MCP surface

Launch the same binary as a stdio server:

```sh
/absolute/path/polis-wired --socket /private/state/daemon.sock --fixture bench-rpi4b mcp
```

Tools mirror CLI operations: `status`, `images`, `console_read`, `console_write`,
`power`, `cycle`, and `run`. `run` takes optional `image`/`sha256` together,
`auto_recover` (default false), and required `confirm_fixture`. Power-changing
tools also require the exact fixture name. The MCP process emits only protocol
traffic on stdout. Tool errors are returned as tool failures, with no retry.
Client cancellation/disconnection does not revoke an already accepted run;
query status before issuing another destructive operation.

## Logs and interruption behavior

CLI/MCP requests produce a numbered `received` line with the action and relevant
parameters, then `completed`, `started run_id=...`, or `failed` with elapsed
milliseconds. Both frontends use the same socket API and are labeled `CLI/MCP`;
the daemon does not infer which frontend sent a request. Console-write payloads
are summarized by byte count. Workflow stages and final outcomes are printed
separately, because accepting a run is not completing it. Internal Pico
keepalives do not produce client-request logs.

UART display joins split USB chunks into lines, escapes control/non-ASCII bytes,
and includes Pico timestamps and stream offsets. CR/LF terminates lines; idle
partial lines appear after approximately 500–750 ms. Long lines are split at
1024 bytes, and stream gaps/sessions never join unrelated data. Changes in UART
error/overrun/loss counters are also printed. A bounded console queue prevents a
slow terminal from blocking Pico traffic; omissions are reported and full raw
capture remains available. Display limits live in `daemon/src/settings.rs`.
These daemon logs go to stderr; CLI JSON and MCP stdout remain protocol output.

`state_dir/uart.bin` retains received UART bytes; `uart.jsonl` records Pico
sessions, timestamps, offsets, and their positions in that raw log. Host memory
retains the newest 64 KiB for reads/readiness; overflow is reported, and capture
uncertainty prevents automatic reflashing. Before classifying readiness, the
daemon checks fresh Pico UART error, hardware-overrun, queue-loss, and USB
failure counters. This also catches loss of the final UART chunk, which has no
following offset to expose a gap. Intentional off-state discards are counted
separately from queue/USB loss.

`current.json` is an fsynced journal. `runs/<run-id>/` stores the run's status,
selected plan, rpiboot stdout/stderr, boot diagnostic counters, and successful
flash verification details.
Failures retain the initial missed-readiness reason even after a successful
recovery. A process restart marks an active journal interrupted and performs no
continuation. Lost command/flash acknowledgements are uncertain, not permission
to replay. Failures leave power/selection as last commanded rather than issuing
an automatic cleanup power cycle while storage state may be unknown.

## Software checks

```sh
# Cargo workspace:
cargo test --locked
cargo build --locked
cargo firmware --locked
cargo clippy --locked --all-targets -- -D warnings
cargo clippy --locked -p polis-wired-firmware --target thumbv8m.main-none-eabihf --profile firmware -- -D warnings
```

From the repository root, `python3 -m unittest tests.test_wired_storage` tests
selection, hash pinning, refusal paths, and write/readback using temporary files.
`python3 -m unittest tests.test_wired_frontend` exercises CLI/MCP against a fake
Unix-socket daemon after building the native binaries. Rust workflow tests use
a simulated backend; they do not execute rpiboot or write physical storage.
Hardware acceptance remains the separate checklist in [recovery.md](recovery.md).

## Boot benchmarking and firmware logs

Run `make wired-diagnostics` to retrieve the current boot capture, or
`make wired-benchmark ARGS="--samples 5"` to start repeated normal cold boots.
Use `make wired-cli ARGS=status` for progress, summary and artifact paths.
See [measurement definitions, protocol and acceptance checks](benchmark.md).
