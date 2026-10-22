# Polis kernel logo

`polis-splash.png` is an unchanged copy of the user-supplied 512×512 image at
`/home/broosegoose/Downloads/polis_icon.png`. It is opaque and retains its teal
background. Its SHA-256 is
`2f5afd4c2c57e757939350be8227dae4c275dd2aa0692d7d4ebdeccb4ba884fa`.
The screen profile embeds this image as its single centered kernel boot logo.
`polis-splash.ppm` is the preconverted ASCII PPM used by the kernel, preserving
512×512 dimensions with at most 224 colors. `board/rpi4/kernel-logo.mk` copies
it into the kernel when `BR2_POLIS_KERNEL_LOGO=y`. Normal builds require no
ImageMagick download or compilation.

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

The kernel uses DRM fbdev emulation and fbcon to draw the logo before userspace.
The PPM is a build input, not a target file; the PNG is its source artwork.
The earlier 60×60 icon is reference
artwork only. Keep serial console logging enabled rather than using `quiet`,
which suppresses the kernel logo in the pinned kernel.
