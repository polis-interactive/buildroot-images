# Polis SBC Controller

Microcontroller-based power, recovery, and serial-console control for embedded SBCs.

Pico 2 W fixture for Pi 4B power control and UART capture, with a Tokio/Kameo
host daemon and one CLI/MCP frontend. A separate direct host-to-Pi USB link
supports RAM-based recovery and verified SD reflashing. The base `polis_*` images also enable a direct USB serial diagnostic endpoint.

Read [the recovery architecture and OTP/wiring prerequisites](docs/recovery.md)
and [host configuration and commands](docs/host.md). Pi power control, UART,
direct USB diagnostics, GPIO8 OTP recovery entry, RAM recovery, verified SD
reflashing, and automatic recovery from a missing-kernel image are hardware
verified. The Pi now uses a dedicated 5 V supply through the GP15-controlled
N/P MOSFET circuit and USB-C adapter. A cold recovery boot and 30-second
four-core load check reported no undervoltage/throttling flags; the earlier
laptop-fed setup did report undervoltage. See the
[commissioning record](docs/recovery.md#commissioning-progress--2026-10-09).
This workspace is host tooling plus Pico firmware, not a Pi Buildroot package.

Read [boot benchmarking and automated log retrieval](docs/benchmark.md) for
`make sbc-controller-diagnostics`, `make sbc-controller-benchmark`, and timing limits. Benchmarks
require the new Pi base image and matching control-link Pico firmware/daemon.

## Build and test

Run from this directory. Rustup installs the pinned toolchain/target on first
use; dependencies need network access until cached.

```sh
cargo test --locked
cargo build --locked
cargo firmware --locked
cargo clippy --locked --all-targets -- -D warnings
cargo clippy --locked -p polis-sbc-controller-firmware --target thumbv8m.main-none-eabihf --profile firmware -- -D warnings
```

`cargo fmt --all -- --check` checks formatting. Native defaults contain the portable crates, daemon, and CLI/MCP. Do not build `--workspace` for the native target: firmware
requires `thumbv8m.main-none-eabihf`. The `firmware` alias applies the target and
optimized profile explicitly. There is one workspace lockfile and no global
embedded target, allocator, or BSP crate. CYW43 is initialized only to drive the
onboard LED; no network stack, Wi-Fi association, or Bluetooth is started.

The linked ELF is
`target/thumbv8m.main-none-eabihf/firmware/polis-sbc-controller-firmware`.
To build and flash, hold BOOTSEL while connecting the Pico's USB cable, release
it, and run from the repository root:

```sh
make sbc-controller-flash
# Select a particular Pico when more than one is connected:
make sbc-controller-flash PICO_SERIAL=4C5A203268260E2C
```

The target builds the current firmware, verifies the flash contents, and starts
the program using `picotool`. `PICO_SERIAL` is the BOOTSEL USB serial, not the
Pi's serial. `CARGO` and `PICOTOOL` can override executable paths. For an already
built ELF, the equivalent command from this workspace is:

```sh
picotool load -v -x -t elf target/thumbv8m.main-none-eabihf/firmware/polis-sbc-controller-firmware
```

Flashing/resetting the Pico switches the Pi off. Flashing is never part of a
Cargo build. Runtime logs use defmt over SWD/RTT, separate from the USB protocol.

Start the host daemon from the repository root with `make sbc-controller-daemon`.
No config file is needed: fixture constants live at the top of
[daemon/src/settings.rs](daemon/src/settings.rs). The daemon runs in the
foreground; Ctrl-C stops it. Pi reflashing is enabled for the commissioned bench
with a pinned SD identity, RAM payload, and known-good image. Automatic recovery
still requires an explicit `--auto-recover` on each run and permits one attempt.
See the [host guide](docs/host.md).

The daemon terminal logs CLI/MCP requests and outcomes, workflow stages, and
received Pi UART lines. Readiness markers and UART errors are highlighted;
`local/polis-sbc-controller-state/uart.bin` retains the original bytes. Rebuild/restart the
daemon to pick up logging changes; no Pico reflash is needed for host logging.

## Fixture wiring

Pin assignments and USB descriptors live in [firmware/src/board.rs](firmware/src/board.rs).
Use the **signal names** below as wire labels. The two Pico columns identify
the GPIO and its physical header position separately. Pi destinations explicitly
name the Pi's GPIO and header pin; UART TX connects to the other board's RX.

| Signal name | Purpose | Pico GPIO | Pico header pin | Connect to |
| --- | --- | --- | --- | --- |
| `HOST_USB` | Daemon connection and Pico power | — | Micro-USB socket | Development host USB port |
| `UART_TO_PI` | Pico transmits → Pi receives | GP0 / TX | 1 | Pi **RXD**, GPIO15 / physical pin **10**, at 3.3 V logic |
| `UART_FROM_PI` | Pi transmits → Pico receives | GP1 / RX | 2 | Pi **TXD**, GPIO14 / physical pin **8**, at 3.3 V logic |
| `GND` | Shared ground | GND | 3 | Pi ground and external power-switch ground |
| `PI_RECOVERY_EN` | High selects recovery boot | GP14 | 19 | **Input/gate of the external open-drain driver**; its output connects to Pi BCM8 / physical pin **24**. See [recovery wiring](docs/recovery.md#connections-and-electrical-contract). |
| `PI_POWER_EN` | High enables Pi power | GP15 | 20 | **Enable input of the external high-side 5 V switch** that supplies the Pi |
| `LED_PI_POWER` | Lit while Pi power is commanded on | GP16 | 21 | Power LED through its own series resistor; LED cathode to GND |
| `LED_DAEMON_TRAFFIC` | Blinks on daemon USB traffic | GP17 | 22 | Daemon traffic LED through its own series resistor; LED cathode to GND |
| `LED_PI_UART` | Blinks on Pi UART traffic | GP18 | 24 | UART traffic LED through its own series resistor; LED cathode to GND |

The onboard **daemon connection LED** needs no external wiring. The separate
host-to-Pi recovery USB cable connects to the Pi's USB-C port through the
[power-isolating recovery wiring](docs/recovery.md#connections-and-electrical-contract).

**Wiring change:** move the power-switch control from GP16 to GP15, and recovery
control from GP17 to GP14 before using this firmware. Pi-side BCM8 is unchanged.
Connect each external LED from its GPIO through its own suitable current-limiting
resistor and LED to ground (active high, 3.3 V). All three start off. The power
LED follows the switch command; it does not measure the Pi's actual supply rail.

The onboard LED flashes at 1 Hz until a valid daemon handshake, then stays on
while the daemon sends requests. A read-only Status keepalive runs every second,
including during long cycles. USB reset/disconnection or four seconds without
an accepted request returns it to flashing and switches Pi power off, cancels
any pending cycle/Boot, and aborts UART transmission. Recovery selection remains
unchanged; a presence timeout preserves request replay protection. Activity
pulses are 100 ms on with at least 50 ms off, so sustained traffic continues
blinking. Heartbeats and
keepalives count as daemon traffic, even when no user command is running.

All pin/peripheral assignments are grouped in `assign_resources!` at the top of
`firmware/src/board.rs`. The onboard LED is **CYW43 GPIO0**, not RP2350 GP25.
Its separate Embassy task uses internal GP23/24/25/29, PIO0 SM0, and DMA_CH0,
following [Embassy's Pico 2 W blinky example](https://github.com/embassy-rs/embassy/blob/main/examples/rp235x/src/bin/blinky_wifi.rs).
The published driver uses one DMA channel; the current upstream example uses two.
The firmware, CLM, and board NVRAM are embedded in the ELF with
[pinned provenance and the vendor license](firmware/vendor/cyw43/README.md).
LED initialization/writes have deadlines and run independently of power control,
UART forwarding, and watchdog feeding. The onboard LED becomes available after
CYW43 initialization; hardware behavior still needs qualification.

Use a switch/driver rated for the Pi's supply and startup load, with a hardware
pull-down so reset defaults to off. A Pico GPIO is a control signal, not a Pi
power source and not a direct drive for an arbitrary MOSFET gate at 5 V.
Keep ground connected; isolate the 5 V supply rather than switching ground.
Avoid other paths powering the Pi, including other powered USB connections.

The UART TX pad is disabled while commanded power is off. Aborted bytes drain
internally before TX is enabled again. This is not electrical isolation or
power-good sensing; provide suitable powered-off protection for the UART and
verify the switch circuit before connecting the Pi. UART initialization briefly
selects the TX function before the software output-disable override is applied.

USB VID/PID `c0de:cafe` are **bench-only placeholders**, not an assigned product
identity. The serial string is the RP2350 OTP chip ID as 16 hexadecimal digits.
The daemon discovers the `Polis SBC Controller` USB product name and exchanges Hello.
USB baud/DTR/RTS
settings do not control the Pi UART; `ConfigureUart` does.

## Protocol

The host and Pico use `polis-control-link-tokio` and
`polis-control-link-embassy` with their `serial` features. Both depend on the
shared `polis-control-link-protocol` and `polis-framing` from
[polis-rs, branch `main`](https://github.com/polis-interactive/polis-rs/tree/main/crates/control-link).
Cargo dependencies track that branch while `Cargo.lock` pins the resolved
commit (`c3abc0e5138b9af3081bd6ac0a2e04b34abb0cb4` for this migration).
Use locked builds; updating the shared branch dependency is an explicit lockfile
update followed by host tests and a firmware link build.

Firmware also enables the shared `supervisor` feature. The library's `usb::Runner`
owns USB service, readiness waits, reset handling, supervision, and reconnection.
The firmware entry point runs it alongside the device controller; there is no
application-owned link task. `ConnectionHandler` implements product message
handling, and `Lifecycle` callbacks invalidate queue epochs on connection loss.
The application supplies a 500 ms send deadline and 1 ms reconnect delay and
retains loss accounting and fail-safe power behavior.

**Migration: rebuild/restart the daemon and reflash the Pico together.** The
shared control envelope is incompatible with the former Polis schema-3 wire
format. The library reports its standard protocol mismatch for that envelope;
this application adds no firmware-version gate. The CLI/MCP JSON API and Pi
image format are unchanged. Hardware qualification recorded
below predates this adapter migration; this version still needs the USB/reset
and stalled-reader acceptance checks.

The frame remains `00 | COBS(header + payload + CRC-32C) | 00`.
The shared header uses protocol ID `0x5043`, envelope schema `1`, and message
IDs Request `1`, Accepted `2`, Response `3`, Event `4`. Its 28-byte payload
prefix contains the stable application ID `polis-wired-ctrl`, nonzero session,
and request ID (zero only for events). The shared codec owns protocol, envelope
schema, and application-ID validation.

[The product codec](crates/embedded-api/src/lib.rs) supplies Postcard Command and
Reply bodies, with Accepted represented by the shared nonterminal envelope.
Events distinguish UART RX and heartbeat; their session lives in the envelope.
Postcard enum order and field layout form the product body contract.
Maximum application body is 256 bytes, total framing payload budget is 284
bytes, and UART chunks remain at most 128 bytes. Strict body decoding rejects
trailing bytes and oversized payloads.

The shared adapters retain partial/coalesced frames across reads, including
across Hello, and the Embassy serial adapter handles 64-byte USB packets and
terminating zero-length packets. USB packets are not message boundaries.
CRC detects corruption, not authentication. The application ID is a compatibility
filter, not a security identity.

Start each host connection with Hello using a fresh nonzero random 64-bit
session nonce and nonzero request ID. Hello returns the chip ID, random boot
ID, firmware version, buffer limit, and status. IDs must increase within the
session; the same nonce in another Hello does not reset their high-water mark.
Start a fresh session before IDs wrap. Do not reuse old session nonces.
USB resets invalidate queued requests from that connection. New Hello aborts
an outstanding UART write and discards queued console output from the old session.

| Command | Behavior |
| --- | --- |
| Hello / Status | Identify and inspect uptime, commanded power, active cycle, UART state, and counters |
| SetPower | Set on/off; rejects conflicting changes during an active cycle |
| CyclePower | Off for at least 100–60,000 ms, then on; `Accepted` followed by `Completed` |
| SetRecovery / Boot | Select only while off, or coordinate off → strap selection → timed on; see recovery contract |
| ConfigureUart | 9,600–921,600 baud, 8N1; only while off, idle, and outside a cycle; default 115,200 |
| WriteUart | One outstanding nonempty write, up to 128 bytes; `Accepted`, then `Completed` after UART drains |

UART completion means bytes left the transmitter, not that the Pi executed a
command. Set/configure return `Completed`; rejected commands return a typed
error. A powered-off Pi rejects writes. Power-off, a new Hello, or USB reset
aborts an outstanding write: some prefix may already have reached the Pi.
Write progress has a one-second deadline; failure/timeout aborts the write and
increments the UART error counter. The host must treat a missing response or
an abort as an uncertain/partial result and **must not automatically replay a
power cycle or UART write**. Duplicate IDs are rejected, not re-executed.

Each UART RX event carries its session, byte offset, bytes, and Pico uptime
when the batch was read. Timestamp resolution does not imply per-byte arrival
precision. Offsets advance even across deliberate software drops; hardware
overruns are counted separately because the exact missing byte count is unknown.
Heartbeats publish status every second after Hello. Counters are cumulative
since Pico boot. Responses carry the originating key; clients must filter old
session responses and console events.

## Power and scheduling contract

Cold boot and watchdog reset default to power off. USB reset, removal, or
unconfiguration switches the Pi off on the next controller pass. Closing or
losing the daemon while USB remains attached switches the Pi off after four
seconds without an accepted request. The controller checks presence before
processing requests or advancing power cycles, so a late keepalive cannot hide
an expired deadline.

Loss of daemon presence also cancels any pending CyclePower/Boot and aborts UART
transmission with the TX pad disabled. Reconnection or renewed keepalives leave
power off until an explicit power-on, cycle, or Boot command. Recovery selection
is preserved; a Pico reset returns to off/normal. A new Hello on a still-live
connection does not cancel an active cycle. Completion reports the GPIO action,
not measured rail voltage or Pi readiness.

The main Embassy task cooperatively polls the shared USB runner and device
controller, alongside independent indicator tasks. Within the controller,
a 1 ms timer services power/TX
progress concurrently with interrupt-buffered UART RX and queued requests.
No controller operation awaits USB output. A two-second watchdog is fed only
by controller progress. Active cycles can extend their off interval while
aborted UART output drains internally; a Pico reset interrupts the cycle and
returns to off.

Memory is bounded: UART RX ring 2 KiB, UART TX ring 256 bytes, 16 console events,
8 incoming requests, and 8 control responses. Full input queues return Busy
when response space permits. Control-response overflow is counted; actions are
not rolled back because their response was lost. Console overflow drops new
chunks and counts bytes. USB output chooses control responses before heartbeat
and console traffic, with a 500 ms frame deadline if the host stops reading.
Completed frames never interleave. A timed-out or failed send may be partial:
both link halves are retired, the USB epoch advances, queued work is invalidated,
and the controller switches power off/cancels pending work on its next pass.
A fresh Hello is required; commands are never automatically replayed. In-flight
console loss is counted even when USB reset cancels transmission. This stricter
failure handling follows the shared sender's cancellation contract.
Loss is observable, not promised impossible.

## Validation and remaining hardware checks

Host tests cover fixed envelope/body fixtures, arbitrary stream splits,
worst-case message sizes, invalid requests, session/duplicate handling, and
power-cycle deadlines. In-memory tests connect the real Tokio and Embassy
adapters and exercise long-operation keepalives, rejected probes, late replies,
stale events, and send timeouts without replay. Low-level framing corruption
and USB packetization tests live in polis-rs. The firmware must also compile and link for RP2350;
`cargo check` alone is insufficient. These checks do not establish physical
USB enumeration, UART signal integrity, power timing, or watchdog recovery.

Before connecting a Pi, validate with the switch load disconnected using the
host daemon and these hardware acceptance checks:

1. Enumerate USB and exchange Hello/Status; confirm the reported chip ID.
2. Loop GP0 to GP1 and test framed binary UART data while commanded power is on.
3. Observe GP15, its GP16 power LED, and TX through power-off and timed cycles.
4. Disconnect/reconnect USB data while independently powering the Pico; verify
   immediate power-off and cycle cancellation. Stop the daemon with USB attached
   and verify power-off after four seconds, including during a pending Boot/cycle.
   Reconnect and confirm power stays off until explicitly commanded on. Separately
   verify cold-reset power off.
5. Saturate UART/USB queues and stop host reads; confirm power timing continues
   and loss counters expose the overflow. Check GP17/GP18 visible pulses under
   sustained traffic, and onboard blinking → solid → blinking on daemon start,
   stop (USB still attached), and USB reset. Repeat during a long power cycle.

Rebuilt `polis_*` images emit kernel UART logs and `POLIS_SMOKE_READY init`
without boot-partition edits. They also answer a direct USB diagnostic ping:
run `make sbc-controller-usb-test` from the repository root after flashing and booting.
See [the base image defaults](../../README.md#product-services-and-diagnostics).
No UART shell is enabled. USB diagnostics do not establish recovery readiness.

The USB construction follows ControlTesterCode's Embassy CDC setup, adapted
for RP2350 and stream framing.
