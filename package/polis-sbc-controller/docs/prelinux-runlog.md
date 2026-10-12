# Pre Linux boot optimization runlog

This is the persistent experiment record for `bench-rpi4b`. Keep unsuccessful
changes: a result for one kernel, firmware and configuration does not establish
the result for a later combination. Raw evidence stays in the referenced local
run and snapshot directories; this tracked document preserves the conclusions.

**Current profile direction, 2026-10-11:** `polis-rpi4-speed` selects P09,
including P06 cut-down firmware and P08 `NET_INSTALL_ENABLED=0` board provisioning.
`polis-rpi4-base` contains only musl, EROFS and static core policy over upstream.
P08 remains an explicit EEPROM update outside image builds. Composed speed
assembly checked with saved P09 inputs: byte-identical LZ4 kernel payload,
premerged DTB and firmware pair, equivalent active firmware settings/cmdline,
and a 6 MiB inner FAT. This is assembly validation, not a new hardware benchmark.
The legacy `polis_rpi4_musl_erofs` defconfig retains the standard-firmware variant
below; use the named speed profile for P09.

**Earlier source direction, 2026-10-11 (legacy defconfig only):** omit P06 at the user's request to
avoid firmware restrictions on future camera/display use. Select standard
`start4.elf`/`fixup4.dat` with `config-premerged.txt`; keep P07's premerged
overlays and P09's boot ramdisk, enlarged from 6 to 8 MiB to fit the standard
firmware. Other optimizations remain unchanged. The headless kernel/config
still needs separate camera/display setup. This revised combination has not
been boot-tested or benchmarked; existing snapshots and measurements below
remain historical evidence and include P06 where stated.

Assembly validation passed using the existing kernel/rootfs and the updated
genimage recipe in `/tmp/polis-without-p06-7iizxv8s/`: complete SD image generated,
8 MiB inner FAT confirmed, standard firmware bytes verified, and inner/outer
configs checked for matching firmware selection with no `gpu_mem=16` or
duplicate overlay directives. This was a local assembly check, not a hardware
flash or a new boot-time measurement.

**Latest hardware-verified state: P09 SD image with P08 EEPROM configuration.** Median
**4.316770 s** across five cold boots; **3.032903 s lower** than the original
saved baseline. Detailed evidence and hashes are in the P09 entry below.
P10–P12 showed no demonstrated incremental benefit and were reverted.
Final P09 restoration and normal capture are verified. Experiments stopped
after P12 at the user's request; retain the 64-bit OS. The saved median is
**41.27% lower** than the original saved baseline.

## Measurement and decisions

Use `enable_to_marker_observed_us`: actual Pico power-enable transition to
observation of `PLIS_ENTRY_V1`, both on the Pico clock. Keep schema-3 marker
instrumentation, 115200 8N1 UART and 1000 ms power-off interval unchanged.
This includes serial delivery/task latency; it is not rail-rise to first CPU
instruction. Firmware intervals use firmware timestamps only. Linux readiness
is a functionality check, not the score. See [benchmark.md](benchmark.md).

Signed differences below are **candidate minus its stated baseline**: negative
is a lower observed median, positive is higher. Direction alone does not prove
a causal benefit or regression. Keep every failed or outlying boot. Distinguish
observed differences from a demonstrated benefit, and identify the comparison
configuration rather than assuming that effects add together.

**User direction, 2026-10-09:** reuse the saved baseline results; do not run a
fresh baseline for every experiment or routinely repeat A/B baseline batches.
Benchmark candidates against the recorded baseline. Preserve uncertainty from
drift/outliers rather than spending repeated runs to force a conclusion.
A verified restoration boot is a functionality check, not a new baseline batch.

Each new entry records the hypothesis before editing, exact source change,
parent baseline and retained settings, individual timings and median/range,
image hashes, run IDs, functionality/power checks, decision, uncertainty and
interaction hypotheses. For a combination, name its constituent experiment IDs
and compare against its immediate parent as well as the original baseline.
Do not claim synergy or antagonism without testing the combination; a four-way
comparison of baseline, X, Y and X+Y separates interaction from simple addition.

The long-term Goal is **speed up pre-Linux boot time**. Reuse the existing
Buildroot output and healthy daemon; keep one build and fixture workflow at a
time. Preserve UART, USB capture, required features, instrumentation and both
original snapshots. No EEPROM/OTP/wiring/voltage/overclock changes without
separate approval. A single automatic recovery attempt ends that batch for
review; identity changes, invalid timing, undervoltage or hardware issues stop
further experiments. The older recovery image is not benchmark-capable.

Human-readable summary: [pre-linux-kernel-speedups.tsv](../../../benchmarks/pre-linux-kernel-speedups.tsv).
Times are seconds rounded to 0.001 s; descriptions are second-to-last. Full-precision individual measurements, identities and hashes remain in the evidence and this runlog.
Negative delta means a lower observed median; it is not by itself evidence of a causal speedup. The older `boot-times.csv` has only its header; its userspace readiness columns are inappropriate here and remain separate. Valid slow boots are retained and flagged.

**Updated direction:** test substantial reversible bundles inspired by the research, not just one-setting experiments. Each bundle records its parent and components. Reuse saved baselines.

## Results at a glance

| ID | Change and parent | Observed median difference | Evidence and decision |
| --- | --- | ---: | --- |
| P01 | Gzip level 6 kernel payload; original benchmark baseline | +39.0 ms pooled (+0.53%); +60.473 ms first five | No demonstrated benefit; reverted. Full-metric uncertainty from drift/outlier. Firmware loading plus post-load interval consistently about 64.8 ms slower. |
| P02 | Skip LCD detection with `ignore_lcd=1`; original benchmark baseline | +15.768 ms (+0.21%) | No measurable effect; consistent with baseline variation. Rejected; valid slow boot retained. |

| P03 | `max_framebuffers=0`; original benchmark baseline | -71.335 ms (-0.97%) | No measurable effect; consistent with baseline variation. Setting remains unchanged in P04. |

| P04 | Size-optimized slim kernel; P03 firmware configuration | -0.971 s (-13.21%); -0.900 s vs P03 | Clear reduction in kernel read (~1.019 s); keep. Five valid boots; early-firmware slow boot retained. |

| P05 | P04 kernel, LZ4 payload and cached ARM64 decoder | -1.349 s (-18.35%) vs original; -0.378 s vs P04 | Keep; identical decoded kernel and userspace. Five valid boots; slow sample retained. |

| P06 | P05 plus cut-down firmware / `gpu_mem=16` | -1.498 s (-20.38%) vs original; -0.149 s vs P05 | Omitted from current source at user request (2026-10-11): future camera/display use. Five historical valid boots; VideoCore log unavailable. |

| P07 | P06 plus pre-applied UART/Bluetooth and USB overlays | -1.600 s (-21.77%) vs original; -0.102 s vs P06 | Small observed difference; benefit unconfirmed. Retain provisionally; five valid boots, no recovery. |

| P08 | P07 SD image plus EEPROM `NET_INSTALL_ENABLED=0` | -2.725 s (-37.08%) vs original; -1.126 s vs P07 | Clear improvement; keep. Same EEPROM firmware version; five valid boots, no recovery. |

| P09 | P08 plus 6 MiB FAT16 boot filesystem loaded into RAM | -3.033 s (-41.27%) vs original; -0.308 s vs P08 | Keep. Five valid boots, unchanged kernel/firmware/EEPROM, no recovery. |

## Common baseline and fixture

Original benchmark image: `.snapshots/polis_rpi4_musl_erofs-benchmark-v1/images/sdcard.img`.
SHA-256: `5e3dc56590f3af34018c65c9750bee9f5b0f221284180f068d3502813a6f7665`.

Preserved automatic-recovery image: `.snapshots/polis_rpi4_musl_erofs-well-known/images/sdcard.img`.
SHA-256: `847af678eb5e732c61322d64d2c7362489f7f4b58b1a4b886211b14dd5acece3`.
This fallback lacks current benchmark/capture instrumentation. Never substitute
its boot time for a candidate score or automatically replace it with a winner.

Hardware: Pi 4B, revision `d03115`; Pico ID `4c5a203268260e2c`; Pi USB topology
`3-2`; SD LUN serial `1000000000e4bb49`. Source settings and runtime identity
checks remain authoritative; this record does not authorize guessing a disk.

Firmware: standard `start4.elf`, version
`2226a853bb9f5fd80392e3a4a89e457aeca88008`, built 2025-12-08 19:39:51.
EEPROM: `57db150d63864d47e6c9071f6b086a5401eb4e92`, release 2025-11-05 17:28:26.
Linux: `6.12.61-v8`, AArch64, instrumented uncompressed `Image`, 22,868,480 bytes.
Baseline retains `disable_splash=1`, `force_eeprom_read=0`,
`disable_poe_fan=1`, `enable_uart=1`, `disable-bt` and DWC2 peripheral overlays.

The commissioned baseline median was 7.330413 s, range 7.273997–7.395510 s,
run `1791595916038-cafcd9c8e85f5602`. Power-health evidence in these experiments
is limited to retained logs: the existing normal-image capture protocol has no
live `get_throttled` query. The earlier dedicated-supply qualification is not
repeated or represented as a fresh sticky-flag/load test.

## P01 Gzip kernel payload

Date: 2026-10-09, America/Chicago. **Decision: reverted; no demonstrated benefit.**

Hypothesis: reducing the approximately 1.906 s SD kernel-read window would save
more time than firmware decompression costs. The only experimental variable was
payload encoding. `genimage-erofs.cfg` generated `gzip -n -6` bytes under the
same FAT filename `Image`, preserving FAT insertion order. No kernel rebuild;
decompressed bytes matched baseline exactly. Payload shrank from 22,868,480 to
9,060,081 bytes. Firmware/DT/cmdline and rootfs file contents were unchanged.

| Batch | Individual times in seconds | Median s | Min–max s |
| --- | --- | ---: | --- |
| Baseline A1 | 7.360303, 7.293745, 7.363777, 7.316005, 7.394540 | 7.360303 | 7.293745–7.394540 |
| Gzip B1 | 7.420776, 7.516378, 7.454069, 7.333122, 7.346122 | 7.420776 | 7.333122–7.516378 |
| Gzip B2 | 7.382897, 7.415594, 7.424846 | 7.415594 | 7.382897–7.424846 |
| Restored baseline A2 | 7.423253, 10.877720, 7.453571 | 7.453571 | 7.423253–10.877720 |

Eight boots per image: baseline median **7.3791585 s**, gzip **7.418185 s**;
difference **+0.0390265 s**. B1−A1 was +0.060473 s; B2−A2 was −0.037977 s.
The reversal and overlap prevent a precise overall regression claim. No timing
was discarded. The 10.877720 s baseline outlier's first retained firmware
message was at 7.808757 s versus approximately 4.32 s on adjacent boots; its
first-message-to-ARM interval was normal at 2.910196 s. The earlier delay's
cause remains unknown. This is a valid slow boot, not an invalid marker.

The mechanism was more consistent: first-five median firmware kernel-read
window fell 1.906267→0.756939 s, but load-to-ARM grew 0.003376→1.217487 s.
Combined interval grew 1.909639→1.974466 s (**+64.827 ms**). This is consistent
with firmware decompression cost; it is not an isolated decompressor timer.

All 16 boots passed init readiness, UART marker/counter validation and automatic
hash-verified USB capture. EROFS root and writable data mount checks passed;
D-Bus/iwd startup messages remained successful. No recovery or voltage/throttle
warnings occurred. Existing `regulatory.db` warnings persisted; Wi-Fi association
was not tested. Original firmware/EEPROM versions and instrumentation above
were identical across measured boots.

Candidate: `.snapshots/polis_rpi4_musl_erofs-prelinux-gzip-v2/images/sdcard.img`;
SHA-256 `490f5f2baa13b1838ca08875fbe3009b7a842a86ef75ae617198e3454f3a91a3`.
Packaging v1 was never flashed; v2 corrected an incidental file-order change.
Source was reverted and original benchmark-v1 restored with complete readback
verification. Build output still held candidate artifacts until a later build;
installed-image provenance comes from the flash journal, not the output folder.

| Evidence | Run ID |
| --- | --- |
| Baseline A1 | `1791596748155-8b9838822f38ed03` |
| Candidate flash | `1791596912824-e657d15e81d9e90a` |
| Candidate B1 / B2 | `1791597004098-6151ca926634cce7` / `1791597078173-40d43ad03c796f5c` |
| Baseline restoration / A2 | `1791597126874-153800d89dfd59d1` / `1791597245856-5326ed349347c62a` |

Detailed local report, exact reverted patch, hashes, captures and outlier analysis:
`.local/polis-recovery/experiment-20261009-prelinux-01/`.

**Interaction hypotheses, untested:** payload size/content and compression level
change the transfer/decompression balance. Slower storage could favor gzip;
faster transfer could favor uncompressed data. A smaller kernel could change
both costs. Display-probe changes are expected to affect another phase, but
their combination with gzip has not been measured. Do not permanently blacklist
compression or add the measured differences to predict combinations.

## P02 Skip LCD detection

Date: 2026-10-09, America/Chicago. **Decision: rejected; no demonstrated benefit.**

Fresh baseline run `1791597817036-ad4fafcacd5545da`: 7.389417, 7.304441,
7.371390, 7.340217, 7.349673 s. Median 7.349673 s; range 7.304441–7.389417 s.
All five timings/captures passed; no repeat of P01's early-firmware outlier.

Hypothesis before editing: `ignore_lcd=1` may remove part of the approximately
0.46–0.52 s gap between the second config read and display-related firmware
messages. That gap is not an isolated LCD detector measurement. This headless
profile already excludes graphics and has no required DSI display functionality.
The experiment will add only `ignore_lcd=1` to `board/polis/rpi4/config.txt`;
uncompressed Image and all diagnostic paths remain identical to the baseline.

[Official Raspberry Pi documentation](https://www.raspberrypi.com/documentation/computers/config_txt.html#ignore_lcd)
defines the option as skipping touch-display detection. Applicability and any
timing benefit must be checked on our actual firmware; no saving is assumed.
Firmware reinstall and image assembly should suffice without a kernel rebuild.

Potential interactions, untested: LCD detection, HDMI hotplug and framebuffer
allocation may share firmware display setup. Their effects may overlap rather
than add. Keep separate entries for individual and combined tests.

Evidence directory: `.local/polis-recovery/experiment-20261009-prelinux-02/`.

## Earlier hypothesis backlog (before P03/P04)

| Idea | Evidence and constraint |
| --- | --- |
| HDMI hotplug suppression / zero firmware framebuffers | Repeated EDID attempts span about 33 ms within two explicit groups; other display gaps unisolated. Headless-only candidates; framebuffer tested as P03. Later user direction favors substantial bundles. |
| Smaller kernel or preprocessed DT | Kernel read is approximately 1.906 s; DT/overlay activity also measurable. Preserve required modules, runtime firmware fixups, USB/UART and entry hook; higher implementation/build cost. |
| Early boot-source work | First firmware message around 4.2 s, occasionally much later. No isolated earlier transcript. Captured EEPROM omits BOOT_ORDER; documented default is SD first. EEPROM changes require separate approval. |

Earlier investigation kept standard firmware to retain VideoCore timing detail.
The later user direction favors substantial blog-inspired changes, and the
existing exporter/reporting code explicitly supports cut-down firmware with
unavailable firmware timestamps. P06 below tests that documented variant while
preserving Pico instrumentation and the complete UART/USB capture path.
Old ARM32 appended-DTB toggles cannot be copied directly; required USB removal,
overclocking and userspace tuning remain outside this pre-entry experiment.
The requested historical research links and applicability notes are in P01's
local `hypothesis.txt`; their headline times do not use this metric.

## P02 measured outcome

Candidate samples: **7.365441, 7.349686, 7.462297, 10.916434, 7.311366 s**.
Median **7.365441 s**, range **7.311366–10.916434 s**. Compared with saved P02
baseline median 7.349673 s: **+15.768 ms (+0.21%)**, within ordinary variation.
**Classification: no measurable effect, consistent with baseline variation.**
The numerical difference is not a demonstrated slowdown.
Against the original qualified median 7.330413 s: +35.028 ms; these controls
are recorded separately, not mixed into a new baseline.

The setting was active and LCD pin messages disappeared, but the config-to-
restart gap remained around half a second. The long sample is included and
localized using the firmware's own first-message-to-ARM interval in
`.local/polis-recovery/experiment-20261009-prelinux-02/result.json`.
No additional baseline batch was run after the user's instruction to reuse
controls. Five valid boots passed the same UART, capture/hash, mount and
startup checks as P01; no recovery or power warnings. No fresh power-flag or
Wi-Fi association claim. Versions and instrumentation match the common baseline.

Candidate snapshot: `polis_rpi4_musl_erofs-prelinux-ignore-lcd-v1`.
SHA-256: `96ea1c12f89d800402cb6b51381bca1a11b83cfc5e333bd4c8722472f80ebed3`.
Flash run: `1791597927165-1bc2ba2ac19e6d58`; benchmark:
`1791598018202-97ebe2abc376c2d2`. Source option is removed for the next test;
this verified working candidate was replaced by P03 at the next verified
flash. No intervening baseline reflash or benchmark is needed.

## P03 Disable firmware framebuffers

Hypothesis recorded before editing: set only `max_framebuffers=0` relative to
original benchmark-v1, with P02's `ignore_lcd` removed and P01 gzip absent.
The repeated approximately half-second display/configuration gap remains a
candidate for unnecessary firmware display setup. The official option prevents
firmware framebuffer allocation in headless mode; any timing benefit is a
hypothesis, not guaranteed by that documentation. This profile already omits
Linux graphics and does not require HDMI/DSI output.

[Official option](https://www.raspberrypi.com/documentation/computers/legacy_config_txt.html#max_framebuffers).
Legacy firmware setting is being tested on our pinned standard Pi4 firmware,
not assumed applicable to Raspberry Pi OS KMS configurations. No diagnostic
path, firmware variant, memory split, clock, kernel or DT overlay is removed.
Use saved P02 baseline five-boot median 7.349673 s as the primary comparison;
original qualified and P01 measurements remain context for variability.
Potential display-setting interactions remain untested. Evidence:
`.local/polis-recovery/experiment-20261009-prelinux-03/`.

## P03 measured outcome

Samples: **7.278338, 7.278724, 7.311859, 7.245995, 7.249676 s**.
Median **7.278338 s**, range **7.245995–7.311859 s**. Difference from the saved
P02 baseline: **-71.335 ms (-0.97%)**; from qualified baseline: -52.075 ms.
**Classification: no measurable effect, consistent with baseline variation.**
The numerical difference is not a demonstrated speedup. Median first retained
firmware timestamp shifted from 4.238381 s to 4.170500 s, accounting for most
of the observed difference before the trace can identify its cause. The setting
remains in the already-started P04 build solely to keep its firmware config
unchanged. It is not counted as a proven optimization; no new baseline batch.

Snapshot: `polis_rpi4_musl_erofs-prelinux-no-fb-v1`; SHA-256
`09eef186024a8ad210ed44f083fffdde5df861c9ca54c771477b721854ea3afb`.
Flash: `1791598159502-9260397e11ec5b79`; benchmark:
`1791598389313-79f0b6a8c9c4d698`. All five passed the established validation;
no recovery or power warnings. This image was installed through P03; P04 below identifies the current image.
The firmware config-int query omits the requested zero (baseline printed -1);
exact FAT config plus full SD readback establish the deployed setting. The query
is not claimed to report a literal zero.

## P04 Substantial kernel reduction — keep

Parent: P03, retaining `max_framebuffers=0`. Use the saved common baseline
7.349673 s and P03 7.278338 s; no new baseline batch. Hypothesis: shrink the
22,868,480-byte uncompressed payload and its approximately 1.906 s SD read.

Bundle in `board/polis/rpi4/linux-prelinux-slim.fragment`, scoped to the EROFS
board: `-Os`; remove tracing/profiling/eBPF syscall support, all-symbol data,
virtualization, containers, 32-bit compatibility, unused initramfs/PCI/SCSI/
NVMe/RAID, network/F2FS filesystems and input/HID/IR support. Keep ordinary
printk/kallsyms/debugfs, firmware capture, SMP/preemption, security mitigations,
SD/MMC, EROFS/ext4/FAT, Wi-Fi/crypto, GPIO, PL011 and DWC2 USB diagnostics.

This adapts the small-kernel approach in [instant-pi](https://github.com/AndreVallestero/instant-pi)
and [Furkan Tokac's experiment](https://furkantokac.com/rpi3-fast-boot-less-than-2-seconds/).
Their ARM32 compression/appended-DTB mechanisms are not copied into ARM64.
The gain is attributable to the bundle only; individual components and their
interaction with gzip remain untested. Size optimization can also change
post-entry runtime performance, which this timing metric does not measure.

Evidence: `.local/polis-recovery/experiment-20261009-prelinux-04/`. An unexpected
rootfs/diagnostic dependency can prevent boot. Preserve existing recovery and
stop this batch if it activates. Record the final effective Kconfig delta.

P04 build completed incrementally in approximately 8 minutes. Image is
10,621,440 bytes, down from 22,868,480 (53.55% smaller). Effective Kconfig
changes: 509 symbols. Required feature and marker checks passed; all userspace
ELFs are AArch64. Extracted rootfs content/modes/symlinks outside `/lib/modules`
are identical to P03. FAT differs only in `Image`; DTB, firmware, configuration
and overlays match. Partition layout is identical. The loose historical
`Image.gz` in the output/snapshot is unused and absent from the FAT image.

Candidate: `polis_rpi4_musl_erofs-prelinux-slim-v1`, SD SHA-256
`77d2177e04c49c5d7b5d18c2785b1da64bdd71f40abe4ab118d38c296019a785`.
Kernel SHA-256: `20c9d62b7371ceba4a0de388f1007d17256abedcbe47b0fa59a28f6216b22b74`.

### P04 measured outcome

Samples: **6.403165, 6.378715, 9.924458, 6.323550, 6.327385 s**.
Median **6.378715 s**, range **6.323550–9.924458 s**. Saved common-baseline
difference: **-0.970958 s (-13.21%)**. P03 difference: **-0.899623 s**; the
P03 setting itself still has no demonstrated effect. No baseline was rerun.

The firmware kernel-read median fell from P03's **1.906573 s to 0.887849 s**,
a repeatable **1.018724 s** reduction. The post-load interval remained roughly
0.0034 s. This mechanism and the nearly one-second median change support a
real bundle benefit despite remaining early-boot jitter. Do not attribute the
gain to any individual removed subsystem without separate experiments.

The **9.924458 s** boot remains included, with its full timing and firmware
intervals in `result.json`. Its early delay is consistent with the earlier
valid slow boots; the retained firmware trace does not identify the cause.
All five boots passed unchanged Pico timing/identity/counter checks, unique
Linux IDs, marker, UART, captured-file hashes, read-only EROFS root, writable
ext4 data, D-Bus/iwd startup and Wi-Fi firmware loading. No module compatibility
errors, recovery, panic or voltage warning appeared. Wi-Fi association and
fresh sticky power flags were not measured. The existing missing regulatory.db
message remains; the removed cgroup support also makes the firmware-injected
`cgroup_disable=memory` parameter unrecognized and ignored. Neither prevented
the checked startup/diagnostic paths.

Flash/readback: `1791599115591-4b1423d5052d5fa3`, all 327,156,224 bytes verified.
Benchmark: `1791599212547-4acaf6eca9488485`. **Image installed through P04:**
`polis_rpi4_musl_erofs-prelinux-slim-v1`, SHA-256 as recorded above. Both original
baseline and automatic-recovery snapshots remain preserved.

Decision: **keep P04**. Further payload/DT work should compare with this result
and the saved original baseline. Gzip and kernel size may interact; P01 does
not establish the outcome of compressing this new kernel. The effects of
`-Os` on runtime throughput and the bundle's individual components remain
unmeasured. The table is a human summary; retain exact sample evidence here.

## P05 ARM-side LZ4 loader — keep

Hypothesis: P04 still spends about 0.888 s reading 10.62 MB from SD. Wrap the
exact same kernel in a small self-relocating ARM64 loader with an LZ4-HC block.
The 5.147 MB payload should save roughly 0.45 s of SD reading, less ARM-side
relocation/decompression/cache-cleaning costs. Parent P04 median: 6.378715 s;
common saved baseline: 7.349673 s. No new baseline batch.

This adapts the compressed small-kernel idea to our actual architecture.
[ARM64 boot protocol](https://www.kernel.org/doc/html/latest/arch/arm64/booting.html)
requires bootloader decompression; ARM32's built-in zImage decoder is absent.
The new loader uses the [LZ4 block format](https://github.com/lz4/lz4/blob/v1.10.0/doc/lz4_Block_format.md)
and our observed Pi firmware EL2 entry. It temporarily identity-maps low RAM
with caching, relocates beyond Linux's full Image/BSS reservation, decodes back
to the original load address, cleans caches and restores EL2 state before
entering Linux with its original DTB. It never emits the benchmark marker.
**The Pico score includes all loader work before the unchanged Linux marker.**

Scope: Pi4 raw-image EL2 entry only; not EFI or arbitrary ARM64 machines. The
loader rejects unsupported entry state, invalid/overlapping FDT and malformed
LZ4. Firmware, DTB, overlays, UART settings, clocks, userspace, recovery and
the P04 kernel bytes remain unchanged. The Image header reserves the entire
relocation/stack area; no fixed external scratch address is assumed. Source:
`board/polis/rpi4/lz4-loader/`; EROFS genimage packs its result as FAT `Image`,
preserving file insertion order and leaving the loose raw kernel untouched.

Preflight evidence: host decoder passed actual-kernel round-trip, truncated
and undersized-output cases plus 3000 randomized cases under AddressSanitizer
and UBSan (leak scanning disabled because the execution harness uses ptrace).
A locally extracted Ubuntu QEMU ARM package, with no system package installation,
booted the AArch64 loader and verified the exact original kernel body via an
independent checksum, valid DTB, zero reserved arguments, EL2 and MMU/data-cache
off at handoff. Unsupported EL1 entry is rejected. These tests do not replace
real Pi boot/capture/power validation.

Prototype: 10,621,440 raw bytes -> 5,138,486 compressed bytes; complete loader
payload 5,146,688 bytes. The decompressed SHA-256 remains P04's
`20c9d62b7371ceba4a0de388f1007d17256abedcbe47b0fa59a28f6216b22b74`.
Evidence: `.local/polis-recovery/experiment-20261009-prelinux-05/`. Risks include
firmware placement and cache/entry-state assumptions; one automatic recovery
ends this batch. P01 firmware-gzip results cannot predict ARM-LZ4 performance.

### P05 measured outcome

Samples: **9.493105, 5.911830, 6.001027, 6.033973, 5.901449 s**.
Median **6.001027 s**, range **5.901449–9.493105 s**. Difference from P04:
**-0.377688 s**; from the saved original baseline: **-1.348646 s (-18.35%)**.
All four ordinary boots are faster than every ordinary P04 boot; the valid
slow boot is retained in the median/range. No baseline reruns.

Firmware kernel-read median: **0.432515 s**, versus P04 **0.887849 s**.
The firmware's roughly 0.0034 s after-load interval ends at ARM startup and
therefore does **not** measure the new ARM decoder. The unchanged Pico marker
is emitted by the decoded Linux kernel and includes all loader time. No clocks
were cross-subtracted to invent a decompression-duration measurement.

Slow sample: first retained firmware message **7.808116 s**, followed by
**1.467691 s** to ARM startup on that firmware clock. The SD read remained
**0.432515 s**. This repeats the earlier pre-trace delay pattern; its cause
is still unisolated, and no sample was discarded.

All five passed the established timing, identity, counters, unique Linux IDs,
UART marker, captured-file hashes, EROFS/ext4 mounts and service checks.
Additional checks confirmed all four CPU cores, EL2 startup, Wi-Fi firmware
loading, unchanged kernel/firmware/EEPROM versions, command line and string
configuration; no module mismatch, recovery, panic or voltage warnings.
The source FAT config and complete rootfs content/modes/symlinks match P04.
No Wi-Fi association or fresh sticky-power-flag test was claimed.

One additional strict runtime-config comparison initially failed because sample
3 omitted `over_voltage_avs=10000`. Archived original baseline sample 3 also
omits that field, as do samples in P01–P04. Every other config-int entry matched.
`extended-validation.json` retains the exact candidate and saved-baseline
observations. This is pre-existing query variation, not an applied voltage or
clock setting change; its underlying firmware behavior was not newly diagnosed.

Snapshot: `polis_rpi4_musl_erofs-prelinux-arm-lz4-v1`. SD SHA-256:
`ee727846820f33954620f5ef246d6f5de5757fc1f4088bce9494cc0654dc47f2`.
Wrapped payload SHA-256:
`75bf55fc42bbfc885b9aca83aada5118817efd98365d19fb85789f419b4c302d`.
Flash/readback: `1791599820490-80162aea66d09091`, all 327,156,224 bytes verified.
Benchmark: `1791599912232-6ee37ca73ec7370b`.
**Image installed through P05: prelinux-arm-lz4-v1.** The original
benchmark, P04 snapshot and configured older recovery image remain preserved.

Decision: **keep P05**. Benefits depend on kernel size, codec, CPU caching and
SD throughput. P01 tested firmware gzip, so its negative result does not
contradict this ARM-LZ4 result. LZ4 on the original larger kernel and separate
loader optimizations remain untested. Emulator verifier sources, extracted
QEMU package and exact sanitizer/fixture results are retained in P05 evidence.
The tracked decoder test is `board/polis/rpi4/lz4-loader/test-decode.c`.

## P06 Pi4 cut-down firmware — omitted from current source

Originally retained provisionally; superseded by the 2026-10-11 user direction
above. The following records the historical experiment without changing its
measurements.

Parent P05: 6.001027 s; common saved baseline: 7.349673 s. Keep the exact
P05 LZ4 payload, kernel, rootfs, instrumentation, UART and power-off interval.
Switch only the firmware variant/memory split needed for that variant:
`gpu_mem=16`, packaged `start4cd.elf` and matching `fixup4cd.dat`, from the
existing pinned firmware revision. The standard firmware is 2,303,232 bytes;
cut-down is 849,948 bytes. Hypothesis: smaller transfer and initialization
work can save hundreds of milliseconds; measure with Pico, not assumptions.

[Official firmware selection](https://www.raspberrypi.com/documentation/computers/config_txt.html#start_file-fixup_file)
requires `gpu_mem=16`; forcing the cut-down files via `start_file`/`fixup_file`
alone is unsupported. Scope this to the EROFS board with `config-cutdown.txt`;
other board profiles keep their standard firmware configuration. No EEPROM,
OTP, supply voltage, clock or wiring change. Existing single recovery then stop.

**Diagnostic tradeoff:** cut-down removes VideoCore debug logging. Keep every
existing capture command and USB transfer unchanged; preserve its explicit
firmware-error record and leave unavailable firmware metrics null. GPIO/Pico
measurement, UART, kernel logs and USB diagnostic retrieval remain requirements.
The existing `src/diagnostics/bootbench.py` already identifies `(start_cd)` and reports
missing firmware timestamps as expected n/a; the exporter saves firmware query
errors without discarding the rest of the capture. This is the documented
later firmware experiment in README, after the standard-firmware investigation.
The earlier runlog's blanket exclusion of cut-down firmware was too strict.

Validate all other diagnostics and product startup before accepting a candidate.
Only the specifically expected unsupported firmware log is exempted from P01's
no-error-file check. Never treat an absent timing as zero or suppress its error.
Potential interactions: changed GPU memory split and firmware initialization
may affect DT fixups and ARM entry, including the P05 loader. These are part
of this firmware variant, not separate attributed wins. Evidence:
`.local/polis-recovery/experiment-20261009-prelinux-06/`.

### P06 measured outcome

Samples: **5.803517, 5.884899, 5.852021, 5.852475, 5.821025 s**. Median
**5.852021 s**, range **5.803517–5.884899 s**. Difference from P05:
**-0.149006 s**; from the saved common baseline: **-1.497652 s (-20.38%)**.
All five are below every P05 sample, but the change is modest and comparisons
remain subject to batch drift. No slow outlier occurred in these five; this
does not establish that earlier outliers are fixed. No baseline was rerun.

Every boot retained a hashed `firmware-error.txt` with the specific message:
`Logs are not available with gpu_mem=16. Increase gpu_mem in config.txt`.
All four derived firmware timing fields are null, never zero. The validator
accepts only this documented log unavailability; it does not blanket-ignore
failed captures or other firmware query errors. Remaining firmware queries,
USB transport/hash checks, UART/marker, unchanged Pico counters/identity,
four CPU cores at EL2, Wi-Fi firmware loading, service startup and EROFS/ext4
mounts passed. No recovery, panic or UART/dmesg voltage warning. Fresh sticky
power flags and Wi-Fi association remain untested.

Active firmware reports commit `2226a853bb9f5fd80392e3a4a89e457aeca88008`,
built 2025-12-08 19:40:47, `(release) (start_cd)`. Kernel and EEPROM versions
are unchanged; `arm_freq=1500`, `core_freq=500`, `enable_uart=1` and UART clock
match P05. Firmware memory split and its generated command-line values are
expected variant differences. No supply-voltage or clock setting was applied.

Snapshot: `polis_rpi4_musl_erofs-prelinux-cutdown-v1`. SD SHA-256:
`532976d514b9d73901668cf3acad8031bba63e069c2a897ea8cbdc0864188f5b`.
Flash/readback: `1791600364106-9962f388197b5b1e`, all 327,156,224 bytes verified.
Benchmark: `1791600473440-915e4940aafb2848`.
**Current installed working image: P06 / prelinux-cutdown-v1.**

Decision: retain the modest gain provisionally for speed testing. P05 remains
available with standard firmware when detailed VideoCore tracing is needed.
The unchanged automatic-recovery image and original baseline remain preserved.
We cannot separate firmware transfer from initialization savings using this
variant's absent VPU log. The paired firmware/memory-split change and its
interaction with P05's loader are recorded as one experiment.

## P07 Pre-applied device-tree overlays

Parent: P06, saved five-boot median **5.852021 s**; original saved baseline
**7.349673 s**. No baseline rerun. Hypothesis before building: pre-merge
`disable-bt` and `dwc2,dr_mode=peripheral` into the packaged board DTB to avoid
their FAT reads and firmware merge work. This follows the static-DT direction
of the posted fast-boot blogs while preserving runtime memory, board, chosen
and UART fixups. Keep `enable_uart=1`, `dtparam=audio=off`, the exact P04 kernel,
P05 LZ4 payload and P06 cut-down firmware. No captured per-boot entropy is reused.

`board/polis/rpi4/premerge-dtb.py` builds the official dtmerge host utility
from the already pinned raspberrypi/utils source and kernel libfdt sources.
Genimage packages its output under the original board-DTB filename, and the
two corresponding dtoverlay directives are removed from `config-cutdown.txt`.
The original loose DTB remains untouched; overlays remain on the FAT volume.
[Upstream dtmerge](https://github.com/raspberrypi/utils/blob/master/dtmerge/README.md)
handles Raspberry Pi override parameters, including DWC2 peripheral mode.

Preflight property comparison found only the expected 17 property changes:
serial aliases, UART/Bluetooth pin state, Bluetooth/mini-UART disablement and
USB compatible/mode/FIFO/status settings. No memory, chosen, Wi-Fi, clocks or
other nodes changed. The firmware still fills the overlay's empty UART pin
properties at boot; normal UART/USB validation is required before timing.
Potential interaction with cut-down firmware remains a hypothesis; compare
against P06 as well as the common baseline, without assuming additive savings.
Evidence: `.local/polis-recovery/experiment-20261009-prelinux-07/`.

### P07 measured outcome

Five cold boots: **5.808988, 5.715049, 5.774125, 5.749910, 5.736649 s**.
Median **5.749910 s**, range **5.715049–5.808988 s**. Observed difference:
**-0.102111 s (-1.74%) versus P06**, **-1.599763 s (-21.77%) versus the saved
original baseline**. Classification: **small observed difference; benefit
unconfirmed**. Retain provisionally as the parent for further substantial
changes; do not count this as an independently proven optimization. P06/P07
ranges narrowly overlap, and drift/earlier boot variation can explain part or
all of this difference. Cut-down firmware cannot provide the stage trace to
attribute it to DT processing. No new baseline batch was run.

Snapshot: `polis_rpi4_musl_erofs-prelinux-premerged-dt-v1`.
SD SHA-256: `7e89ed9311dc80a4e2fabdc1bea98d3dcaa6e77941f46169ec6ab972279a7bb6`.
Flash run: `1791600873994-e5679c457be6b258`; five-boot benchmark:
`1791600985545-1f836c248d08f6ee`. Full **327,156,224-byte** readback matched.
Only the FAT DTB and config differ from P06; complete rootfs content, file
modes and symlinks, kernel, LZ4 payload, firmware and partition layout match.

All five passed unchanged Pico identity/generation/counters, one marker per
boot, capture hashes, UART, USB diagnostics, four CPUs at EL2, Wi-Fi firmware
loading, init/D-Bus/iwd readiness and read-only EROFS/writable ext4 mount checks.
Kernel/firmware/EEPROM versions, command line and clocks match P06. The only
runtime config-query variation is the previously documented optional
`over_voltage_avs` report; no voltage setting changed. VideoCore log remains
unavailable as expected for cut-down firmware; other capture errors are absent.
No voltage/throttling/panic warnings appeared in UART or dmesg. This is not a
fresh sticky-flag, load, or Wi-Fi association qualification.

No failures or fallback attempts occurred. **P07 is installed and verified**;
P06 and both original snapshots remain preserved. The result does not establish
synergy with kernel slimming, LZ4, or cut-down firmware independently.

## D01 Early boot diagnostic (not a speed candidate)

After P07, one temporary SD image added only `uart_2ndstage=1` to config.txt.
No EEPROM setting, UART baud, marker or power interval changed. Extra bootloader
logging changes workload, so this boot is deliberately unscored and is not a
new baseline. Snapshot: `polis_rpi4_musl_erofs-prelinux-early-trace-v1`, SHA-256
`7e54325ae06097a650371adddec821c70e8606dda413ba424c681f5655c73c58`.
Run `1791601156914-39646bf103b1d493` passed verified flashing and normal capture,
with unchanged versions/configuration and no fallback or power warnings.

The retained normal-boot UART begins after config is read and shows firmware
file loads followed by `XHCI-STOP`, controller registers and `PCI0 reset` before
the kernel marker. This demonstrates XHCI cleanup, consistent with bootloader
USB initialization, but does not isolate the earlier time spent probing USB.
The printed timestamp fractions are not used to derive durations here.
Source config was restored and original clean P07 reinstalled and verified in
run `1791601275458-35c04ab09d15cfc8`; no baseline batch was repeated.
Evidence: `.local/polis-recovery/diagnostic-20261009-early-boot/`.

## P08 Disable bootloader network-installer probing

Parent: P07 SD image, saved median **5.749910 s**. Common original baseline
**7.349673 s**. Hypothesis: stop the bootloader's network-installer keyboard scan
and associated USB initialization before loading firmware. Official documentation
states that network install defaults on for Pi 4 and adds approximately one
second of boot time. The captured EEPROM config omits this setting; the pinned
firmware includes the feature. This is a stronger early-stage hypothesis than
another Linux/kernel-size change, but its effect on this fixture remains to be
measured. [Official setting](https://www.raspberrypi.com/documentation/computers/raspberry-pi.html#NET_INSTALL_ENABLED).

**Authorization:** the user explicitly approved this EEPROM experiment and then
requested writing it as a documented, scripted post-firmware step. Scope:
append only `NET_INSTALL_ENABLED=0`, retain bootloader firmware release
`57db150d63864d47e6c9071f6b086a5401eb4e92`, original boot order/defaults,
UART and GPIO-forced USB recovery. No OTP, supply or clock changes.

Source scripts: `board/polis/rpi4/post-firmware/{prepare,apply}.py`, documented
in that directory's README. They are separate from normal builds. Preparation
uses the pinned official 2025-11-05 firmware binary (SHA-256
`3bf21d52b020a7e76a5fe9903e714a7fb5272c0a3a7bc1c1410f9cf45ed8b933`), replaces
only boot configuration plus the normal self-update timestamp, checks unchanged
executable sections, and adds only `pieeprom.upd`/`pieeprom.sig` to a temporary
copy of P07. No rootfs or partition bytes outside the FAT change.

EEPROM update SHA-256:
`8adbbf0f4b10567f16a7faa7ac676a8280a687161fcd52683f2e59662d11132a`.
Temporary SD updater SHA-256:
`718d1f2b06321919285dab50b5b2c62637bffbb32cb3343b55706fd928e615db`.
The earlier offline proposal omitted the fresh update timestamp; these are the
final staged artifacts. The apply script validates live before/after config,
uses the existing daemon's verified flash workflow, then restores the clean P07
SD image before benchmarking. Update/reset and cleanup boots are not scored.

`before.conf` and a reconstructed same-release `rollback.bin` are preserved;
this is not a physical EEPROM dump. Rollback preparation must assign a fresh
update timestamp. Automatic SD recovery does not undo EEPROM configuration.
The first preparation attempt caught read-only permissions inherited from the
snapshot copy before any fixture action; preparation now makes only its copied
files owner-writable. Failed local artifacts remain preserved.

Evidence: `.local/polis-recovery/experiment-20261009-prelinux-08/`. Potential
interaction: removing early bootloader work may reduce the large pre-trace
outliers, but five boots alone cannot establish that it eliminates them. The
same SD image hash must henceforth be paired with its EEPROM configuration.

### P08 measured outcome

Five cold boots: **4.626353, 4.624276, 4.624852, 4.573754, 4.607948 s**.
Median **4.624276 s**, range **4.573754–4.626353 s**. Difference from saved P07:
**-1.125634 s (-19.58%)**. Difference from the saved original baseline:
**-2.725397 s (-37.08%)**. **Clear improvement; keep.** Every P08 sample is
below the fastest saved P07 sample (5.715049 s); the reduction is much larger
than the observed within-batch spread and matches the documented mechanism.
No baseline rerun. The exact causal magnitude remains subject to saved-control
drift; absence of a long outlier in five samples does not prove it is eliminated.

Update workflow: `1791601497168-f9b79f5329e74722`. Clean P07 restoration:
`1791601582368-bcca82bf224026c5`. Both passed full SD readback, normal boot and
captured EEPROM configuration checks without failures or fallback. The clean
restoration also exercised GPIO-forced USB ROM recovery after the EEPROM write.
Benchmark: `1791601671712-04e47333230d8860`. Installed SD is the exact preserved
P07 image, SHA-256
`7e89ed9311dc80a4e2fabdc1bea98d3dcaa6e77941f46169ec6ab972279a7bb6`.
All updater files are absent because that clean image was reflashed and fully
read back before timing. No updater/cleanup timing enters the result table.

Installed EEPROM configuration:

```ini
[all]
BOOT_UART=0
WAKE_ON_GPIO=1
POWER_OFF_ON_HALT=0
NET_INSTALL_ENABLED=0
```

Bootloader version/build timestamp remain unchanged; only update-time metadata
now reads **1791601453**. All five captures verify the configuration above,
the same kernel/firmware versions, command line and clock configuration, four
CPUs at EL2, Wi-Fi firmware loading, UART, USB diagnostics/capture hashes,
init/D-Bus/iwd startup and EROFS/ext4 mounts. Pico boot ID/generation and error
counters are unchanged; one marker was observed per cold boot. No voltage,
throttling, panic or oops warnings were found in UART/dmesg. VideoCore logs
remain unavailable with cut-down firmware; no new sticky-flag/load/association
qualification is claimed.

The post-firmware refusal tests pass: an unfamiliar EEPROM config is preserved
and rejected, a tampered update image never reaches the daemon, and an existing
apply journal prevents blind replay. A local lock-handle cleanup warning found
by these tests was corrected; the hardware sequence was unaffected. The
preparer verifies executable sections, both FAT manifests and all bytes outside
the boot FAT. Exact final scripts, software checks and all measurements are
preserved under the P08 evidence directory.

**Current working combination: P04 + P05 + P06 + P07 SD image, with P08 EEPROM
configuration.** P02/P03 remain unproven, and P07's small individual effect
remains unconfirmed. P08 targets earlier bootloader work; its result does not
establish a specific interaction with the later compression/firmware changes.

## P09 Compact RAM boot filesystem

Parent: P08 (P07 SD contents plus EEPROM `NET_INSTALL_ENABLED=0`), saved median
**4.624276 s**. Hypothesis before building: load a compact FAT filesystem in
one sequential bootloader read, then serve firmware/kernel/DTB reads from RAM,
possibly avoiding later firmware SD setup and slower file loading. No new
baseline batch, EEPROM write, kernel change, clock change or instrumentation
change. [Official boot_ramdisk option](https://www.raspberrypi.com/documentation/computers/config_txt.html#boot_ramdisk).

Genimage creates a **6 MiB FAT16** `boot.img` containing the exact P07/P08
LZ4 payload, premerged DTB, cut-down firmware pair, command line and firmware
config. Required overlays are already merged, so unused overlay files are not
in the ramdisk. The outer 128 MiB FAT retains only config (original plus
`boot_ramdisk=1`) and `boot.img`; no loose kernel/DTB can provide a silent
fallback to the old loading path. Rootfs and partition layout remain unchanged.
The bootloader supports FAT12/16/32; a valid small FAT16 filesystem avoids the
padding overhead of a conventional minimum-size FAT32 volume. No secure-boot
or OTP provisioning is enabled. The public docs recommend FAT32, while the
pinned loader's release notes explicitly include small FAT12 ramdisks; this
candidate deliberately exercises the smaller FAT16 variant.

Potential antagonism: loading filesystem padding and copying files from RAM
may cost more than ordinary file reads. Potential synergy: prior LZ4 compression
and premerged overlays let the entire required boot filesystem fit into 6 MiB.
These mechanisms are hypotheses until measured. Evidence and original genimage
config: `.local/polis-recovery/experiment-20261009-prelinux-09/`.

### P09 measured outcome

Five cold boots: **4.343379, 4.308377, 4.275500, 4.338128, 4.316770 s**.
Median **4.316770 s**, range **4.275500–4.343379 s**. Difference versus saved
P08: **-0.307506 s (-6.65%)**; versus original saved baseline:
**-3.032903 s (-41.27%)**. **Keep: clear observed improvement.** Every sample
is below P08's fastest saved sample (4.573754 s). The difference is larger than
the observed within-batch variation. Saved-control drift remains a limitation;
no new baseline was run and no claim is made that effects add independently.

Snapshot: `polis_rpi4_musl_erofs-prelinux-ram-boot-v1`. SD SHA-256:
`4322c8d87d421030dbbede3ec13bd7bfca01e8378f8d10fe168c1a9934063a8d`.
`boot.img`: **6,291,456 bytes**, SHA-256
`090e2d536273f2d45f670ffdbf4ff5dfd4f6798d7b72bddab2c3d4fcd47c5696`.
Preflight verified 12,159 data clusters (valid FAT16), exact equality of all
six inner boot files to P07/P08, original rootfs content/modes/symlinks and MBR
partition layout. Outer FAT has only config and boot.img, proving successful
boot used the RAM filesystem rather than loose fallback files.

Flash/readback and normal capture: `1791601896494-6dc51def632f4cc3`.
Full **327,156,224-byte** SD readback matched. Five-boot benchmark:
`1791602005463-a41caf1c38b386fa`. All samples passed the same Pico identity,
generation, counters, 115200 UART, one-marker-per-boot, unique Linux boot IDs,
USB capture hashes, four CPUs at EL2, Wi-Fi firmware load, init/D-Bus/iwd
readiness and EROFS/ext4 mount checks. Kernel, firmware, EEPROM version/config,
command line and clock settings match P08. The known optional over_voltage_avs
query field is excluded from normalized config comparisons; no voltage change.

No failures, recovery attempts, voltage/throttling warnings, panic or oops.
Cut-down firmware still cannot supply VideoCore stage logs, so the gain cannot
be apportioned between avoiding SD initialization, different loader throughput,
or RAM access. No fresh sticky-flag, load, or Wi-Fi association claim.
**P09 is installed and verified, with the retained P08 EEPROM setting.** The
P07/P08 SD snapshot and original recovery images remain preserved.


## P10 ARM64 instruction filtering and Zstd

Parent: P09, saved median **4.316770 s**, with P08 EEPROM retained.
Hypothesis recorded before edits: replacing the ARM-side LZ4 payload with
ARM64 BCJ plus Zstd level 19 can shrink the sequential RAM boot read enough
to offset the more expensive decoder. The nested FAT16 filesystem shrinks
from 6 MiB to 5 MiB. This is a combined compression/boot-volume experiment;
its result will not isolate BCJ, codec choice and padding separately.

The original Linux Image is unchanged. Host XZ supplies the ARM64 BCJ encoder;
a bounded inverse adapted from its 0BSD implementation restores the bytes
following Zstd decompression. Buildroot pins host Zstd **1.5.7**, verifies its
source archive, and its upstream amalgamator produces the freestanding decoder.
The existing EL2 entry, self-relocation, cache setup, live DTB preservation
and Linux handoff are shared with P05. New statically reserved memory is
256 KiB for the decoder context and 64 KiB for its stack. No dynamic allocation,
mutable globals, runtime relocations or replacement benchmark marker.
The Zstd frame has an integrity checksum.

Initial payload: **3,554,869 compressed bytes**, **3,620,416 bytes including
loader**, versus P09's 5,146,688-byte wrapper. Host ASan/UBSan checks passed:
exact kernel reconstruction, undersized capacity, truncated inputs, 100
single-bit corruptions, and 512 random/instruction-heavy BCJ round-trips with
unaligned tails. QEMU decoded the actual kernel body and passed the independent
checksum/EL2/cache-off/DTB/reserved-argument verifier; unsupported EL1 was
rejected. These are correctness checks, not Pi timing measurements.
Evidence: `.local/polis-recovery/experiment-20261009-prelinux-10/`.

Potential synergy: stronger compression reduces the entire sequential boot.img
read. Potential antagonism: decoder instruction footprint, Zstd computation,
BCJ pass, workspace/cache flushing, or RAM copies may erase the I/O savings.
Five candidate boots will be compared with saved P09 and original baseline;
no baseline rerun, EEPROM change, clock change or instrumentation change.

A separate USB power-delay lead was rejected without a hardware experiment:
[official EEPROM documentation](https://github.com/raspberrypi/documentation/blob/master/documentation/asciidoc/computers/raspberry-pi/eeprom-bootloader.adoc)
says memory initialization already satisfies the USB power-off interval on
Pi4 revision 1.4 and later; this board is revision 1.5. No claim of measured
benefit or regression is recorded for that untested setting.


P10 build-process issue: invoking standalone `make configure` cleared the
wrapper's incremental-state file (`src/build/commands.py` invalidates it for non-build
actions). The following build therefore unnecessarily ran `linux-dirclean`
and package rebuilds. This was unintended build work, not a boot optimization.
The effective kernel config still exactly matches P09. Future host dependency
additions should go through the normal tracked `make build` configure path,
which retains per-step fingerprints. Generated metadata must be compared before
accepting this candidate; the prototype checks above used the saved P09 Image.


Final P10 preflight passed. Kernel configuration and size match P09; the
rebuild changed **28 bytes only**, in the two generated version/timestamp
strings and the main GNU build ID. All other kernel bytes are identical.
Kernel SHA-256 is now
`acc7ca14743f00c9a6d34b46dba4cf84aeec247d65c5482c0a4215591970bb25`.
Rootfs contents, modes and symlinks match except **five BusyBox timestamp
bytes**; its executable code is identical. These incidental metadata changes
are disclosed rather than claiming byte-identical entire images. Final-kernel
ASan/UBSan and independently checksummed QEMU handoff tests passed again.

Final compressed payload is **3,554,866 bytes**, wrapper **3,620,416 bytes**
(SHA-256 `79e2ce08acfd436f9a1034cceef54161594e41285fc229ba2dfaef1ba4b46d87`).
The **5,242,880-byte** boot.img has 10,127 FAT16 data clusters and SHA-256
`0189c5067ee8bbe9a9727b9ef6314bf8676f0b577fc7fc250fa4a225d9527af7`.
Boot files other than the kernel wrapper, partition layout, EEPROM setting
and benchmark instrumentation are unchanged. Snapshot: `prelinux-zstd-bcj-v1`.
SD SHA-256: `ff6068670a2d807df771fff4d9ae3208469bb6253ce56bcd2d132899c1e0d7d4`.

Preflight tooling issues were confined to local inspection: an initially
incorrect snapshot path, selection of the embedded vDSO GNU note instead of
both GNU notes, and repeated EROFS extraction into an existing directory.
The last attempt altered an inspection directory's permissions; that directory
was preserved and a fresh extraction passed the strict comparison. None of
these attempts operated the fixture. The first hardware run is
`1791603167268-31a4ea19e1c69171`, with full readback and single automatic recovery.


### P10 measured outcome: no demonstrated gain; revert

Five cold boots: **7.915911, 4.302607, 4.302572, 4.249361, 4.217352 s**.
Median **4.302572 s**, range **4.217352–7.915911 s**. Difference versus saved
P09: **-0.014198 s (-0.33%)**; versus original saved baseline:
**-3.047101 s (-41.46%)**. The incremental difference is negligible relative
to variation. **No measurable improvement over P09; revert the active profile.**
The smaller image does not justify retaining the more complex decoder based
on this batch. No new baseline batch was run.

The 7.915911 s first sample remains included. It resembles earlier multi-second
slow-boot outliers, but cut-down firmware lacks stage logs, so its cause cannot
be assigned to the codec or to earlier firmware work. All five are valid
measurements, not timing failures. An I/O saving offset by extra decoder work
is plausible, but the measurements do not isolate those costs. A different
kernel/boot-volume combination could change the result; preserve this codec
for future interaction tests rather than declaring it universally slower.

Benchmark run: `1791603299111-1bf763210bec8682`. All five passed capture hashes,
unique Linux boot IDs, one marker per boot, Pico identity/generation/counters,
115200 UART, two NUMA nodes/four CPUs at EL2, Wi-Fi firmware load,
init/D-Bus/iwd readiness, EROFS/ext4 mounts, and unchanged firmware/EEPROM/
command line/clock configuration. The first extended-check script assumed
one NUMA node; inspection confirmed that both P09 and P10 report two nodes,
four CPUs. The check now uses the actual saved topology. No failures,
automatic recovery, voltage/throttling warnings, kernel panic or oops.
No fresh sticky-flag/load/Wi-Fi association qualification is claimed.

The EROFS genimage config and defconfig are restored to their exact pre-P10
contents. The inactive `zstd-loader/` implementation, candidate snapshot,
source copy and complete evidence remain available. The current output image
artifacts still describe P10 until the next normal tracked build; the preserved
P09 snapshot is used directly for restoration. Do not run standalone wrapper
`configure` merely to rebuild a host dependency.

P09 restore workflow: `1791603385530-9e7cacc7ea5e0404`, SD SHA-256
`4322c8d87d421030dbbede3ec13bd7bfca01e8378f8d10fe168c1a9934063a8d`.
This restore is a functionality check, not an additional baseline measurement.


**P09 restoration complete and verified.** Full 327,156,224-byte readback
matched the preserved P09 SD hash. Normal boot, UART marker, USB capture,
original P09 kernel version, firmware/EEPROM/config, four CPUs at EL2, Wi-Fi
firmware, init/D-Bus/iwd and EROFS/ext4 mounts passed. Pico identity, generation
and error counters remain unchanged. No failed workflow or automatic recovery.
The Pi is powered normally with P09 SD and the retained P08 EEPROM setting.
Restoration evidence: `restored-status.json`, `restored-validation.json` and
`restored-uart.log` in the P10 evidence directory.


## P11 Pi4-specific kernel and deferred data-filesystem drivers

Parent: retained P09 SD with P08 EEPROM, saved median **4.316770 s**.
Hypothesis recorded before editing: reduce the kernel's built-in payload and
keep the fast P05 LZ4 decoder, rather than pay P10's stronger decoder cost.
A smaller RAM boot filesystem should reduce transfer size as well. Ranked
against another codec attempt (P10 found no measurable gain) and unproven
bootloader shortcuts requiring separate approval. No new baseline, EEPROM,
clock, voltage, wiring, or instrumentation changes.

The EROFS-only slim fragment removes alternate Broadcom/Pi5 support, EFI,
Cortex-A72-unsupported SVE/MTE/pointer-auth/BTI features, unused swap,
advanced IPv4 routing/kernel DHCP, MPTCP, traffic control, switch management,
and Ethernet PHY support. Ordinary IPv4/IPv6, multicast, Wi-Fi and every
crypto/security setting remain; security enforcement/logging paths are not
removed. NUMA and the firmware's existing memory policy also remain. UART,
DWC2 USB diagnostics, GPIO, SD/SDIO, EROFS, printk, kallsyms, debugfs and pstore
are preserved. **151 effective Kconfig symbols change**, with the complete
before/after mapping and required-symbol assertions in local evidence.

Ext4, JBD2, mbcache and VFAT become loadable modules. Root EROFS and SD remain
built in. The unchanged mount-data script should cause the kernel to request
`fs-ext4`; existing kmod on the root filesystem resolves the dependencies.
No initramfs is added. The shared FAT core stays built in because MSDOS still
selects it. Host dry-run dependency checks for fs-ext4/fs-vfat passed. Module
loading moves work after kernel entry, so normal /data mounting remains a
required functional check; this is not a claim of faster userspace startup.

Raw Image: **8,818,696 bytes**, versus P09's 10,621,440 (**-16.97%**).
LZ4 wrapper: **4,386,400 bytes**, versus 5,146,688 (**-14.77%**).
The initial 5.5 MiB filesystem fit; a subsequent image-only repack used
**5.25 MiB (5,505,024 bytes)** with 10,635 valid FAT16 clusters. The repack
reused the exact kernel and skipped all package/kernel builds. The first build
correctly retained incremental state and skipped kernel-source cleaning.

Preflight: host exact-kernel decode, truncation/capacity and randomized tests
passed; QEMU verified the complete new kernel body and EL2/cache/DTB handoff,
and rejected unsupported EL1. All non-kernel boot files, outer config and
partition layout match P09. Rootfs differs from P10 only under lib/modules;
P10's already-disclosed five BusyBox timestamp bytes remain the only non-module
difference from P09. Userspace binaries/scripts otherwise remain unchanged.

Potential synergy: reduced compressed I/O, less ARM decoding work and less
RAM boot filesystem padding. Potential antagonism: extra demand-loaded module
work after entry, or a filesystem/module dependency failure. Those must not
be hidden by the pre-Linux score. All cold-boot outliers will be retained.
Evidence: `.local/polis-recovery/experiment-20261009-prelinux-11/`.

Final candidate snapshot: `polis_rpi4_musl_erofs-prelinux-pi4-kernel-v2`.
SD SHA-256: `4264087a49096d1a36a484ab37d7412503101a7ff9e2258758ec8840f82944ba`.
Raw kernel SHA-256: `d1e87dcd71b7d3c8f46e8e5d6ae02613600542465cdb77be81c26d9c367ae54e`.
Wrapper SHA-256: `9f1f2a2e30d6c0efe1a65168c48e7bdcddc02c6b0d309bda18c9e5de963af28d`.
boot.img SHA-256: `6becb7ecbccaad455daba158b684b2fb88498195d7e8286c1460b6ca74a5dbfb`.

First flash/readback and normal-boot workflow: `1791604256682-4d17a589a941fe61`.


### P11 measured outcome: no demonstrated gain; revert profile

Five cold boots: **4.259792, 4.322488, 4.357383, 4.170437, 4.180058 s**.
Median **4.259792 s**, range **4.170437–4.357383 s**. Difference versus
saved P09: **-0.056978 s (-1.32%)**; versus original saved baseline:
**-3.089881 s (-42.04%)**. The incremental difference lies within observed
variation and overlaps P09 substantially. **No demonstrated benefit; revert
this kernel profile rather than claim a speedup.** No baseline rerun.

Benchmark: `1791604371866-8b940d0d82307665`. All five boots passed the existing
marker/clock/Pico identity/counter/capture-hash checks and functional checks:
`/data` is ext4 read-write after automatic module loading,
root is EROFS read-only, four CPUs at EL2, Wi-Fi firmware loaded,
init/D-Bus/iwd ready, UART/USB capture intact. Firmware, EEPROM configuration,
command line and clocks match P09. No failures, automatic recovery, power
warnings, panic or oops. No fresh sticky-flag/load/association claim.

Smaller raw/compressed kernels remain useful evidence, but do not by themselves
prove boot acceleration. The snapshot and candidate config copies are retained
for future combinations. Source slim fragment and genimage config were restored
to their exact P09 versions. The next independent FAT-layout test uses P09's
exact saved image contents; it does not inherit P11's kernel or module changes.


## P12 Larger outer FAT clusters, exact P09 payload

Parent: **P09**, saved median **4.316770 s**, with retained P08 EEPROM.
This candidate replaces P11 and restores P09's exact kernel and rootfs; it
is not an incremental comparison with P11. Hypothesis: a larger allocation
unit may reduce FAT traversal and SD transfer-command overhead while the
bootloader reads the unchanged 6 MiB boot.img. Closed firmware may already
coalesce contiguous reads, in which case this setting will not help.

Only the outer 128 MiB FAT16 filesystem is rebuilt: sectors/cluster **4 → 32**,
cluster size **2 KiB → 16 KiB**. `genimage-erofs.cfg` records `-F 16 -s 32`
for this filesystem. Volume ID and label are preserved. The extracted
config.txt and boot.img are byte-identical to P09. All bytes before and after
the boot partition are identical, including MBR, rootfs and data partition.
Actual boot partition offset remains **512 bytes**; this experiment does not
change partition alignment. There are 8,188 valid FAT16 data clusters; the
two files use 385. Host fsck.fat reports no errors.

This is an image-only rebuild from the immutable P09 snapshot, using the same
Buildroot-host mkfs.fat/mtools. The local preparation script verifies the source
SD hash, both extracted manifests, FAT geometry, volume metadata and all bytes
outside the FAT, then makes the candidate images read-only. No kernel rebuild
or new baseline batch is needed. The earlier candidate kernel outputs remain
in the Buildroot output directory; they are not used in P12.

Potential synergy: one large boot.img exposes sequential-read savings from
larger clusters more directly than a directory of small boot files. Potential
antagonism: wasted space for small files or no change if firmware already merges
reads. No EEPROM/clock/voltage/OTP/wiring or benchmark changes. UART 115200 and
1000 ms power-off interval remain. Five cold boots will be compared with saved
P09; include all failures/outliers and stop after any automatic recovery.

Evidence and candidate: `.local/polis-recovery/experiment-20261009-prelinux-12/`.
Candidate SD SHA-256:
`bb2a5b36c011663a89a69b902ee4459f7a1ebe2564891ebd709bfd94361bc015`.
P09 boot.img SHA-256 remains
`090e2d536273f2d45f670ffdbf4ff5dfd4f6798d7b72bddab2c3d4fcd47c5696`.

Flash/readback and normal-boot workflow: `1791604545344-cae6987a562b37c9`.


### P12 measured outcome: no measurable effect; revert

Five cold boots: **4.343986, 4.349348, 4.373975, 4.287834, 4.303907 s**.
Median **4.343986 s**, range **4.287834–4.373975 s**. Difference versus
saved P09: **+0.027216 s (+0.63%)**; versus original saved baseline:
**-3.005687 s (-40.90%)**. **No measurable effect**: the difference is small
relative to saved/candidate variation, not evidence of a slowdown. Revert the
larger clusters and retain P09. No new baseline batch was run.

Benchmark: `1791604662776-8ad3043dbae3e482`. All five samples passed timing,
marker, Pico identity/generation/counter, unique boot-ID and capture-hash checks;
UART/USB diagnostics, four CPUs at EL2, Wi-Fi firmware load, init/D-Bus/iwd,
and EROFS/ext4 mounts passed. Exact P09 kernel version, firmware, EEPROM,
command line and clocks were verified. No failures, automatic recovery,
voltage/throttling warnings, kernel panic or oops. No new sticky-flag/load/
Wi-Fi association qualification is claimed.

The result does not establish how the closed bootloader coalesces FAT reads.
It only shows that this larger-cluster layout did not demonstrate a useful
change with P09's 6 MiB RAM boot payload. The candidate and source patch remain
preserved. Active genimage config is restored exactly to P09. P11's slim
fragment was already restored; neither rejected experiment remains enabled.
An untested future alignment lead is recorded locally: the original boot FAT
starts at LBA1. No partition-alignment change was made in P12.

### Final restoration and stop after P12

User direction: stop after P12 and retain the 64-bit OS. No further experiments
or 32-bit conversion are authorized by this stopping instruction.

Restoration workflow `1791604757383-8d4c2c7596020cc4` completed at `ready`,
with zero failures and zero recovery attempts. Full SD readback verified P09
SHA-256 `4322c8d87d421030dbbede3ec13bd7bfca01e8378f8d10fe168c1a9934063a8d`.
Normal UART/USB capture, a single entry marker, unchanged Pico counters,
P09 kernel version, firmware/EEPROM configuration, four CPUs at EL2, Wi-Fi
firmware load, services and EROFS/ext4 mounts passed the restoration checks.
Evidence: `.local/polis-recovery/experiment-20261009-prelinux-12/restored-validation.json`.
This was a restoration check, not another baseline batch.

Active kernel fragment and genimage configuration match their saved P09
versions. The installed image is the immutable P09 snapshot; Buildroot's
working output still contains P11 build artifacts and would require a normal
tracked build before reuse. No additional rebuild was run after the stop request.
Retained measured result: **7.349673 → 4.316770 s**, saving **3.032903 s
(41.27%)**, with the 64-bit OS intact. This is the stopping point, not a claim
that every possible 64-bit optimization has been exhausted.
