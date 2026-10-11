# Cold-boot benchmarks and USB log retrieval

Optimization results, signed timing changes and interaction hypotheses are kept
in the persistent [pre-Linux runlog](prelinux-runlog.md).

From the repository root, with the daemon running:

```sh
make wired-diagnostics
make wired-benchmark ARGS="--samples 5"
make wired-cli ARGS=status
```

`wired-diagnostics` reads the current Pi boot's capture without cycling power.
`wired-benchmark` starts 1–20 normal cold boots (default 5), returns a run ID,
and holds the fixture operation lock until it finishes or fails. It does not
flash, enter recovery, or automatically retry a failed sample. Status and UART
reads remain available. Power changes, console writes, reflashing, and other
capture requests are rejected while it owns the fixture. A lost frontend reply
must be resolved using status; never blindly repeat the command.

The same daemon actions are MCP tools `diagnostics` and `benchmark`. The latter
requires `samples` and `confirm_fixture`. Existing explicit-image `run` remains
the flash/verify/boot/recovery workflow. Successful `run` boots now attempt USB
capture too; an older fallback image lacking this endpoint extension still
qualifies as ready, with a saved `capture-error.txt`. Benchmarking requires a
successful capture and a valid entry marker.

## What the number measures

Pico protocol **schema 3** adds `Status.timing`. Reflash the Pico and restart
the rebuilt daemon together; schemas 1 and 2 are deliberately rejected.
Session nonces, monotonic request IDs, fail-off defaults and UART routing are
unchanged. A completed `Boot` or real off→on `SetPower` edge records the request
key and timestamps immediately before/after the GPIO enable write. Repeated
`SetPower on` does not reset the record. `Cycle.power_on_at_us` remains only a
scheduled deadline and is never used as the measurement origin.

The Polis Pi 4 kernel has a small assembly hook at `primary_entry`, before MMU
setup and printk initialization. It preserves boot argument registers and emits
`\nPLIS_ENTRY_V1\n` directly to the existing PL011 UART. It does not use Linux
uptime or buffered kernel log timestamps. It skips MMIO if the MMU is enabled,
requires firmware-initialized UART TX, and uses a finite polling budget so a
stalled UART cannot hang boot. This instrumentation is specific to Pi 4B
physical PL011 address `0xfe201000`; it must not be transplanted to another SoC.
The stock baseline does not receive the hook.

The Pico recognizes the complete marker across UART read boundaries and saves
the observation time in the **same Pico clock** as the enable write. Benchmark
output is `enable_to_marker_observed_us`, with the enable-write bracket width.
Exactly one marker, a fresh power record, an unchanged Pico connection, a new
Linux boot UUID for each sample, and loss-free UART counters are required.

This is a reproducible **power-enable to observed kernel-entry marker** metric,
not an exact electrical rail-rise or CPU instruction timestamp. The 15-byte
marker takes approximately 1.302 ms at 115200 8N1; received-batch interrupt/task
latency is additional and has not been bounded with a logic analyzer. The
initial drain of any preceding firmware UART output can also delay the marker.
Do not subtract an assumed serial/firmware delay to claim exact CPU entry.
Keep instrumentation and UART settings identical when comparing images.
No extra wire is needed. If sub-millisecond accuracy is needed later, qualify a
hardware edge with a logic analyzer or dedicate a Pi→Pico timing GPIO.

The kernel hook is installed by a checked, idempotent pre-build hook. Both a
clean build and an incremental build get the same patch. Its inputs belong to
the incremental kernel rule, so adding the hook preserves existing Linux object
files instead of invoking `linux-dirclean`.

References: [Linux arm64 boot entry contract](https://www.kernel.org/doc/html/latest/arch/arm64/booting.html),
[Raspberry Pi UART/early-console configuration](https://www.raspberrypi.com/documentation/computers/configuration.html#configuring-uarts),
[BCM2711 peripherals](https://datasheets.raspberrypi.com/bcm2711/bcm2711-peripherals.pdf).

## Captures and evidence

The existing `polis-gpio-test` exporter atomically publishes one immutable
`/data/boot-diagnostics/<Linux boot UUID>/` after collecting firmware, kernel,
GPIO readiness, command-line and configuration diagnostics. Its separate worker
keeps file writes off the ready-edge task. Missing firmware tools are recorded
as error files; missing firmware timestamps remain unavailable, never zero.

The Pi USB endpoint now accepts two read-only requests:

- `POLIS_PING`: uptime plus this Linux boot UUID.
- `POLIS_CAPTURE <32 lowercase hex digits>`: a bounded current-boot export.

Capture replies echo the nonce and boot UUID in both BEGIN and END records.
Each whitelisted file has a byte count and SHA-256 followed by base64 lines and
`END_FILE`. Limits are 2 MiB/file and 8 MiB/capture. No caller-selected path,
remote shell, archive extraction, or storage write is exposed. The client pins
USB selection to the fixture's configured Pi topology and product, locks the
TTY, and rejects stale IDs, unknown or duplicate files, corrupt data, oversized
streams, missing trailers and disconnects. Only harmless startup pings and
explicit capture-BUSY replies are retried within the deadline.

Verified files are imported through `src/diagnostics/bootbench.py` into
`local/polis-wired-state/captures/<boot UUID>/`. `capture.json` records hashes,
import time and separate firmware/Linux metrics. Existing UUIDs cannot be
overwritten with different content. Firmware `arm_s` is the firmware's ARM
handoff timestamp; its clock is not added to Linux uptime or Pico timestamps.

A benchmark run stores `benchmark.json` (min/median/max and samples) beneath
`local/polis-wired-state/runs/<run ID>/`. Each `sample-NN/` retains:

- `power-enable.json`: off checkpoint, actual enable record and Pico boot ID.
- `uart.log`: raw bytes from that sample, including failed attempts.
- `capture.json`: reference to the hashed boot capture and firmware metrics.
- `measurement.json`: validated timing, boot identities and final counters.

The journal and global UART event stream retain failure/connection details.
Partial benchmark results have `complete=false`; failed or missing samples are
never turned into zero or silently excluded from a claimed successful run.

## Acceptance status

Native simulations and linked firmware builds are separate from hardware
qualification. Hardware acceptance requires the new Pi image and schema-3 Pico
firmware, followed by capture retrieval and repeated boots with distinct Linux
UUIDs, exactly one marker per boot, stable timing, and unchanged loss counters.
Also inspect the archived firmware logs for missing tools or unsupported
firmware, and test a missing-marker image fails the benchmark without flashing.
An instrumented rail/logic-analyzer latency check remains a separate acceptance
step if precise electrical power-to-CPU-entry timing is needed.

### Bench qualification — 2026-10-09

Hardware verified on the existing Pico/UART wiring with the dedicated switched
5 V supply. The Pi USB cable had moved to port `3-2`; its ROM serial `00e4bb49`
and RAM microSD LUN serial `1000000000e4bb49` were reidentified before enabling
writes. Source constants and installed access rules now pin that port. The
original known-good recovery snapshot was preserved.

Pico schema-3 firmware was flashed and readback-verified using picotool. Snapshot
`polis_rpi4_musl_erofs-benchmark-v1` was flashed via the daemon and fully
readback-verified (SD image SHA-256
`5e3dc56590f3af34018c65c9750bee9f5b0f221284180f068d3502813a6f7665`).
The direct entry marker was observed before the first buffered Linux message.

Benchmark run `1791595916038-cafcd9c8e85f5602` completed five cold boots:

| Sample | Enable → entry marker observed | Firmware ARM handoff, separate clock |
| --- | ---: | ---: |
| 1 | 7.392755 s | 7.233533 s |
| 2 | 7.395510 s | 7.236177 s |
| 3 | 7.330413 s | 7.171076 s |
| 4 | 7.273997 s | 7.114671 s |
| 5 | 7.274883 s | 7.115575 s |

Median **7.330413 s**, range **7.273997–7.395510 s**. All samples had distinct
Linux boot IDs, exactly one entry marker, no new UART capture errors/loss, and
successful automatic retrieval of firmware/kernel logs. All archived file
hashes were independently rechecked. The enable-write brackets were 3–4 µs.
These results retain the transport/rail timing limitations described above.

Evidence: `local/polis-recovery/benchmark-acceptance.json`, the benchmark run
folder, and `local/polis-wired-state/captures/`. First-boot capture negotiation
exposed a USB startup race; the host handshake now tolerates the initial echoed
ping/banner before the raw-mode service answers. All five unattended captures
passed after that host fix. Native tests cover that startup sequence as well as
corrupt/truncated exports, identity mismatches and missing/duplicate markers.
