# CYW43 firmware provenance

Copied unmodified from [Embassy `5bef063886cbc9cf30360e1d1f24520d41781c0f`](https://github.com/embassy-rs/embassy/tree/5bef063886cbc9cf30360e1d1f24520d41781c0f/cyw43-firmware).

Used only to initialize the Pico 2 W CYW43439 and drive its onboard LED.
The board-specific NVRAM is the same `nvram_rp2040.bin` used by Embassy’s
Pico 2 W example. No separate hardware flashing is needed; all blobs are
embedded in the linked Pico ELF. The accompanying Infineon license applies.

| File | SHA-256 |
| --- | --- |
| `43439A0.bin` | `5555e0261da2610a500d68c18d895cace0152bbefbf76f4aa683ebce77e3d7eb` |
| `43439A0_clm.bin` | `e712b3d218e8b1e2747b092e03b8b0afcb8c8c8e355d2a4a0d47b493800f3f89` |
| `nvram_rp2040.bin` | `4904bdbb0c937bd0ac2eb2a1d62f2da4dd90e32082384e02874e8d671b0f330d` |
| `LICENSE-permissive-binary-license-1.0.txt` | `5f65b8a496ac27afda41917c18cb6e690b4a022df1f5a12ea823eb38a287f50e` |
