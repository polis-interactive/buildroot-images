# Polis splash artwork

`polis-splash.png` is an unchanged copy of the user-supplied 512×512 image at
`/home/broosegoose/Downloads/polis_icon.png`. It is opaque and retains its teal
background. Its SHA-256 is
`2f5afd4c2c57e757939350be8227dae4c275dd2aa0692d7d4ebdeccb4ba884fa`.
The screen profile embeds this image in `polis-splash-screen`, its persistent
Rust DRM client. `polis-splash.ppm` is the preconverted ASCII PPM, preserving
512×512 dimensions with at most 224 colors. A Rust host helper converts it to
persistent RGB data; normal builds require no ImageMagick download or compilation.
The older `board/rpi4/kernel-logo.mk` hook remains available behind
`BR2_POLIS_KERNEL_LOGO=y`, but screen-dev disables it.

`make configure`, `make build`, and `make image` check the PNG and PPM content
hashes against `polis-splash.json` before resolving a screen profile's inputs.
When missing or stale, the PPM is regenerated using the host's `magick` (or
`convert`) command. Unchanged artwork needs no ImageMagick installation.
Conversion failures stop before Buildroot runs and preserve the previous PPM.

To prepare the logo manually after replacing the 512×512 PNG:

```sh
python3 -m src.profiles.logo
python3 -m src.profiles.logo --check
```

Commit the PNG, generated PPM, and JSON record together. The converter enforces
512×512 dimensions, ASCII RGB PPM, 8-bit channels, and a maximum of 224 colors.
If using separate `make configure` / `make build` steps, reconfigure after
changing artwork; `make image` does this automatically.

Do not set `BR2_LINUX_KERNEL_CUSTOM_LOGO_PATH`: Buildroot adds
`host-imagemagick` for any nonempty value, including an already converted PPM.

`polis-icon.png` retains the earlier 60×60 reference icon supplied from
`/home/broosegoose/Polis/linktree/ui/assets/brand/polis-icon.png`.
Its SHA-256 is
`b788848ac35becd3363002157232625ac19699bfacfface2aa61bca4aea80057`.
The source repository records its origin as the embedded mobile header PNG
from <https://polis.tv/>.

This reference is not used to regenerate the selected splash.

The screen profile uses direct DRM/KMS and disables fbdev/fbcon. The persistent
logo can be redrawn after boot on HDMI hotplug or DRM lastclose. The PPM is a
build input, not a target file; the PNG is its source artwork. The earlier 60×60
icon is reference artwork only. Serial console logging remains enabled.
