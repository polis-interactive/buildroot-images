# Pi 4 HDMI capture and display development

Build `polis-rpi4-screen-dev`. It composes **all of `polis-rpi4-dev`'s feature
groups**, replacing `headless` with `rpi4-video`; profiles do not inherit TOML
files. Existing profiles are unchanged. SSH keys, `polis-dev.local`, Ethernet,
Wi-Fi setup, persistent storage and `/data/bin/dev-app` behave as described in
[the development guide](development-image.md).
The new group reuses the existing no-getty inittab overlay without selecting
the headless kernel/firmware restrictions, so HDMI does not start a login prompt.

```sh
make image ARGS="--profile polis-rpi4-screen-dev --development"
```

The command uses the pinned Buildroot 2026.08 tree; override its location with
`--buildroot /absolute/path/to/buildroot-2026.08`. Outputs are under
`.output/polis-rpi4-screen-dev-development-<fingerprint>/`: `images/sdcard.img`,
`profile/audit.txt`, `profile/effective-kernel.config`, and `staging/` (the
cross-compilation sysroot). A successful automatic image build also prints its
snapshot directory. `--development` skips EEPROM setup and data expansion.
Use `make configure` with the same arguments for configuration only.

Expanding `/data`, either during final-mode first boot or with host-side
`make flash -- --expand-data`, can leave ext4 initializing new inode tables
in the background (`ext4lazyinit`) when the Pi mounts it. Successful expansion
and flushing do not guarantee that those tables have been initialized.
The ACT LED's `mmc0` trigger
then flashes with SD writes while SSH and Wi-Fi are already usable. Let that work
finish; its duration depends on the card. Check `/sys/class/leds/ACT/trigger`,
`/proc/diskstats`, and the `ext4lazyinit` process before treating sustained
activity as a boot failure. The separate provisioning result is in
`/run/polis-provisioning.json`.

The group enables VC4 display, V3D GLES rendering, Mesa EGL/GBM, libdrm tests
(`modetest`), V4L2 tools (`v4l2-ctl`, `media-ctl`), `kmscube`, and V3DV plus
`vulkaninfo`. Vulkan is reserved for later experiments. No X11, Wayland,
Weston, GStreamer or libcamera is selected. Buildroot's Vulkan tools recipe
builds `vulkaninfo`, **not `vkcube`**.
ALSA capture/playback uses `alsa-lib` and `alsa-utils` (`arecord` and `aplay`).
These also provide the ALSA headers, library and `alsa.pc` in the staging sysroot.

## Board assumptions and kernel details

Use a Raspberry Pi 4B, a compatible TC358743 board on its two-lane CSI camera
connector, and a monitor on the Pi's HDMI output. The capture board's HDMI is
the input; the Pi's HDMI connectors are outputs. Start with an unencrypted
720p60 source. Bandwidth, cable integrity and the capture board determine which
higher modes work; four-lane settings are for compatible Compute Modules.

Firmware uses `vc4-kms-v3d,cma-256`, `tc358743,media-controller`, then
`tc358743-audio`, with automatic camera/display overlays disabled.
CMA reserves 256 MiB for shared DMA buffers.
The Pi kernel pinned by this repository binds `brcm,bcm2835-unicam` to
`CONFIG_VIDEO_BCM2835_UNICAM_LEGACY`, including its media-controller mode.
The upstream `CONFIG_VIDEO_BCM2835_UNICAM` driver instead matches
`brcm,bcm2835-unicam-upstream`; enabling only that driver leaves this overlay
without capture. Recheck this pairing when updating the kernel.

VC4 requires `SOUND`, `SND` and `SND_SOC` in this kernel even for video use.
Capture, I2C, VC4, V3D, BCM2835 I2S, simple-card and the capture stub codec
drivers are built in. VC4's HDMI codec supplies playback; the separate
`tc358743-audio` overlay supplies capture. No `noaudio` KMS parameter is used,
and legacy `dtparam=audio=on` is unnecessary for KMS HDMI playback.
The HDMI DDC controller (`CONFIG_I2C_BRCMSTB`) must also be built in: this
image uses devtmpfs without automatic module loading. Leaving it as a module
keeps VC4 waiting for its I2C adapters, preventing display and HDMI audio
registration even though the V3D render device can appear.
The base's static-core verification remains enabled, with
shared libraries available for graphics and application binaries. Mesa uses
VC4 for display buffer management and V3D for rendering; see
[Mesa's driver documentation](https://docs.mesa3d.org/drivers/v3d.html).

## Bring-up over SSH

Run on the Pi as root after booting the image. Device numbers are examples:
discover the actual nodes before assigning the variables below. Budget about
15 minutes for these checks after connecting the hardware.

1. **Discover capture devices.**

   ```sh
   polis-dev-check
   dmesg | grep -Ei 'tc358743|unicam|vc4|v3d|cma'
   v4l2-ctl --list-devices
   for m in /dev/media*; do media-ctl -d "$m" -p; done
   ```

   Select the media graph containing `tc358743`, its `/dev/v4l-subdevN` node,
   and its connected Unicam `/dev/videoN`. Missing TC358743 indicates an
   overlay, power, ribbon/I2C, or driver-probe problem. Inspect `dmesg` before
   changing userspace libraries.

2. **Advertise an EDID and configure HDMI capture.**

   ```sh
   MEDIA=/dev/media0
   SUBDEV=/dev/v4l-subdev0
   VIDEO=/dev/video0
   v4l2-ctl -d "$SUBDEV" --set-edid=file=/data/capture-edid.bin,format=raw
   v4l2-ctl -d "$SUBDEV" --query-dv-timings
   v4l2-ctl -d "$SUBDEV" --set-dv-bt-timings=query
   v4l2-ctl -d "$SUBDEV" --get-dv-timings
   ```

   Supply a valid EDID from your capture-board vendor or an independently
   validated EDID restricted to supported modes; upload it to the indicated
   path first. Do not blindly copy a 4K monitor EDID. Reconnect the source or
   make it re-read EDID, then explicitly select 1280x720 at 60 Hz. A no-signal
   or no-lock error must be resolved before streaming.

   Use the **entity name and source pad reported by `media-ctl -p`** and the
   detected dimensions. Example for a bridge named `tc358743 10-000f`:

   ```sh
   media-ctl -d "$MEDIA" --set-v4l2 '"tc358743 10-000f":0 [fmt:UYVY8_1X16/1280x720 field:none]'
   v4l2-ctl -d "$VIDEO" --list-formats-ext
   v4l2-ctl -d "$VIDEO" --set-fmt-video=width=1280,height=720,pixelformat=UYVY
   v4l2-ctl -d "$VIDEO" --stream-mmap=4 --stream-count=120 --stream-to=/dev/null
   ```

   Confirm the returned format and successful frame capture. Reconfigure
   timings and both formats whenever the source mode changes. This MMAP smoke
   test proves capture only; it does not prove DMA-BUF interoperability.

3. **Discover display and rendering devices.**

   ```sh
   ls -l /dev/dri
   for c in /sys/class/drm/card[0-9]; do
       printf '%s: ' "$c"; readlink -f "$c/device/driver"
   done
   modetest -M vc4 -c -p
   ```

   Identify the VC4 primary card with a connected HDMI connector and a valid
   mode. V3D's render node performs GPU rendering; a render node cannot own
   KMS scanout. Do not assume `card0` is VC4. Empty connectors usually mean the
   wrong DRM device, a cable/monitor issue, or missing KMS overlay.

4. **Exercise EGL/GLES, GBM and atomic KMS.**

   ```sh
   /etc/init.d/S90dev-app stop
   kmscube -A -D /dev/dri/card1
   ```

   Replace `card1` with the discovered VC4 card. Confirm a visible rotating
   cube and a V3D renderer; stop with Ctrl-C. A software renderer is not an
   acceptable result. `Permission denied`/`Device busy` calls for checking
   DRM master ownership and other display processes. Restore your application
   with `/etc/init.d/S90dev-app start` when finished.

5. **Verify Vulkan discovery.**

   ```sh
   ls /usr/share/vulkan/icd.d
   vulkaninfo --summary
   ```

   Expect a Broadcom/V3D physical device through the V3DV ICD. No desktop
   display is expected; surface-related notices do not by themselves mean
   device enumeration failed. Missing ICD files or no physical device is a
   failure. Record outputs, the image checksum and the Pi serial alongside
   the audit; none of these host configuration checks implies hardware success.

## I²S wiring and EP-0214 uncertainty

The CSI ribbon carries video, **not the TC358743's I²S audio signals**. Connect
the bridge's digital audio output separately to the Pi 4B's 40-pin header.
These are signal names, not a claim about the EP-0214 pad order:

| TC358743 board signal | Pi BCM GPIO / function | Physical header pin |
| --- | --- | --- |
| BCK / SCK (bit clock output) | GPIO18 / PCM_CLK | 12 |
| LRCK / WFS (word/frame clock output) | GPIO19 / PCM_FS | 35 |
| DATA / SD / SD0 (audio data output) | GPIO20 / PCM_DIN | 38 |
| GND | Ground | 39 (or another GND pin) |

The bridge supplies both clocks; the Pi consumes them. The overlay specifies
two 32-bit I²S slots and uses `linux,spdif-dir` as a generic capture codec: the
wire protocol remains I²S, not S/PDIF. GPIO21/PCM_DOUT is not needed for this
capture path, and OSCK/MCLK is not connected by this overlay. Reserve the
PCM pins from other HATs, SPI1 and PWM uses. The wiring follows the pinned
[Raspberry Pi overlay README](https://github.com/raspberrypi/linux/blob/21b410140c47ffab5668399f6f143c7d7b935c8b/arch/arm/boot/dts/overlays/README);
clock roles and slots are defined in the
[audio overlay](https://github.com/raspberrypi/linux/blob/21b410140c47ffab5668399f6f143c7d7b935c8b/arch/arm/boot/dts/overlays/tc358743-audio-overlay.dts).
The README's `tc358743-fast` reference is historical; the pinned overlay source
requires the ordinary `tc358743` overlay, which this profile already loads.

**GeeekPi EP-0214 remains unverified for audio on our hardware.** The
[manufacturer wiki](https://wiki.52pi.com/index.php?title=EP-0214) identifies
the TC358743XBG, advertises solder pads and instructs loading the audio overlay.
It does not establish, for the actual board revision in hand, whether the
necessary I²S nets are routed to usable pads, whether required audio circuitry
is populated, or what voltage/level shifting those pads use. No verified
revision-specific audio schematic or successful capture measurement was
available for this implementation. Do not infer audio functionality from
working CSI video or the chip's capabilities alone, and do not substitute a
Geekworm/C790 board pinout for this GeeekPi board.

Before connecting, power down and verify the pad labels, routing and I/O levels
against a schematic or vendor confirmation for your revision. The Pi header
uses [3.3 V GPIO](https://www.raspberrypi.com/documentation/computers/raspberry-pi.html#gpio);
do not connect 5 V signals or assume that a bare 1.8 V audio output meets the
Pi 4 input threshold. Use suitable level translation if required. Software
cannot repair missing traces, inaccessible signals or absent board components.

## ALSA hardware verification

Allow about 10 minutes after confirming the wiring. Run over SSH as root.
Use an HDMI display/receiver with audio support and enable its speakers.
Card indices can change: use the actual ALSA card IDs shown by discovery.

1. **Discover both audio paths.**

   ```sh
   cat /proc/asound/cards
   arecord -l
   aplay -l
   dmesg | grep -Ei 'asoc|i2s|snd|tc358743|hdmi'
   ```

   Expect a capture card named `tc358743` and VC4 HDMI playback cards such as
   `vc4hdmi0`/`vc4hdmi1`. Card registration alone does not prove connected I²S
   wiring, HDMI audio lock, or valid samples. A missing capture card calls for
   checking the audio overlay and ASoC probe messages. A missing HDMI card
   calls for checking VC4/HDMI codec probing and the KMS overlay.

2. **Verify HDMI output independently.** Upload a known-good stereo PCM WAV
   to `/data/known-good.wav`, then select the connected port's discovered card:

   ```sh
   PLAYBACK='plughw:CARD=vc4hdmi0,DEV=0'
   aplay -D "$PLAYBACK" /data/known-good.wav
   cat /proc/asound/card*/eld*
   ```

   Confirm audible output and sink audio capabilities in ELD. If playback is
   silent, check the physical port, monitor volume and HDMI EDID/ELD before
   debugging capture. `plughw` allows ALSA format conversion for this smoke
   test; it is not a low-latency application configuration.

3. **Prepare the input.** Complete the video EDID/timing setup above. The input
   EDID must advertise stereo LPCM audio at the intended sample rate. Set the
   source to stereo PCM, initially 48 kHz, rather than encoded Dolby/DTS audio.

   ```sh
   v4l2-ctl -d "$SUBDEV" --list-ctrls
   v4l2-ctl -d "$SUBDEV" --log-status
   dmesg | tail -60
   ```

   Check `audio_present` and `audio_sampling_rate` where exposed. Match the
   capture rate to the detected input rate: the generic capture codec does not
   negotiate it with the HDMI receiver.

4. **Record with video streaming active.** The pinned TC358743 driver's
   `enable_stream()` toggles both video and audio buffer enable bits. Keep the
   configured video stream running during this check; stop any competing
   application first. Use the `VIDEO` node discovered above:

   ```sh
   CAPTURE='hw:CARD=tc358743,DEV=0'
   v4l2-ctl -d "$VIDEO" --stream-mmap=4 --stream-to=/dev/null > /data/audio-video-test.log 2>&1 &
   VIDEO_PID=$!
   arecord -D "$CAPTURE" --dump-hw-params -t wav -f S32_LE -c 2 -r 48000 -d 5 /data/hdmi-capture.wav
   kill "$VIDEO_PID"
   wait "$VIDEO_PID"
   ```

   Use supported parameters and the detected input rate if they differ from
   this example. Clockless I²S can stall capture; interrupt `arecord` with Ctrl-C
   if it does not finish, then run the cleanup commands. Inspect the video log
   if capture never starts. For silence or corrupt samples, verify source PCM
   audio, EDID, BCK/LRCK/data signals, electrical levels and sample alignment.

5. **Listen to the captured file.**

   ```sh
   aplay -D "$PLAYBACK" /data/hdmi-capture.wav
   ```

   Confirm recognizable audio without distortion and record card IDs, source
   format/rate, board revision, wiring and image checksum. These manual checks
   are still required after a successful build. Capture and playback use
   independent clocks; live forwarding needs buffering, drift management and
   A/V synchronization in the application. No automatic audio forwarding
   service or fixed default ALSA card is installed.

## Cross-compile Rust against the staging libraries

Build the image successfully first. In your application repository, use the
same output's compiler and sysroot; host `/usr/lib` must not enter target links.
Install the Rust target on the development host if it is not already present.

```sh
rustup target add aarch64-unknown-linux-musl
POLIS_OUTPUT=/absolute/path/to/BuildrootImages/.output/polis-rpi4-screen-dev-development-FINGERPRINT
export CARGO_TARGET_AARCH64_UNKNOWN_LINUX_MUSL_LINKER="$POLIS_OUTPUT/host/bin/aarch64-linux-gcc"
export CC_aarch64_unknown_linux_musl="$POLIS_OUTPUT/host/bin/aarch64-linux-gcc"
export AR_aarch64_unknown_linux_musl="$POLIS_OUTPUT/host/bin/aarch64-linux-ar"
export PKG_CONFIG_ALLOW_CROSS=1
export PKG_CONFIG_SYSROOT_DIR="$POLIS_OUTPUT/staging"
export PKG_CONFIG_LIBDIR="$POLIS_OUTPUT/staging/usr/lib/pkgconfig:$POLIS_OUTPUT/staging/usr/share/pkgconfig"
unset PKG_CONFIG_PATH
"$POLIS_OUTPUT/host/bin/pkg-config" --modversion egl glesv2 gbm libdrm vulkan alsa
CARGO_TARGET_AARCH64_UNKNOWN_LINUX_MUSL_RUSTFLAGS='-C target-feature=-crt-static' \
    cargo build --release --target aarch64-unknown-linux-musl
```

Rust's musl target normally links the C runtime statically. Disabling
`crt-static` for this application allows Mesa/Vulkan shared libraries and
dynamic driver loading; it does not change the image's static-core policy.
Clear conflicting `RUSTFLAGS`/`CARGO_ENCODED_RUSTFLAGS` in the application's
environment. If a crate runs bindgen, install host libclang and pass its
`--target=aarch64-linux-musl --sysroot=$POLIS_OUTPUT/staging` arguments through
the crate's bindgen configuration. No target Rust compiler or libclang is needed.
Inspect the result with the output's `aarch64-linux-readelf -l -d` before using
the existing development guide's SCP update procedure.

The application still must implement buffer lifetime and synchronization:
export V4L2 MMAP allocations with `VIDIOC_EXPBUF`, import compatible planes
with `EGL_EXT_image_dma_buf_import`, render into GBM buffers, and submit atomic
KMS commits. Check formats, pitches, offsets and modifiers across each boundary.
Do not return a capture buffer to V4L2 until GPU access is complete, or reuse a
scanout buffer before its page-flip completion. See the kernel's
[DMA-BUF API documentation](https://www.kernel.org/doc/html/latest/userspace-api/media/v4l/dmabuf.html).
Successful `kmscube` and MMAP capture tests alone establish neither zero-copy
operation nor end-to-end latency.
