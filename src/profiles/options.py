"""Authoritative profile vocabulary; naming a group does not implement it."""
from enum import StrEnum, unique


@unique
class Base(StrEnum):
    POLIS_RPI4_V1 = "polis-rpi4-v1"


@unique
class Capability(StrEnum):
    BOOT_DIAGNOSTICS = "boot-diagnostics"
    UART_DIAGNOSTICS = "uart-diagnostics"
    USB_DIAGNOSTICS = "usb-diagnostics"
    WIFI = "wifi"
    WIFI_SETUP_PORTAL = "wifi-setup-portal"
    ETHERNET = "ethernet"
    DEVELOPMENT_SSH = "development-ssh"
    DISPLAY = "display"
    CAMERA = "camera"
    USB_HOST = "usb-host"


@unique
class Group(StrEnum):
    # Required base groups.
    MUSL_TOOLCHAIN = "musl-toolchain"
    EROFS_ROOT = "erofs-root"
    STATIC_CORE = "static-core"

    # Product features and measurement support.
    POLIS_RUNTIME = "polis-runtime"
    BOOT_DIAGNOSTICS = "boot-diagnostics"
    WIFI_IWD = "wifi-iwd"
    WIFI_SETUPD = "wifi-setupd"
    WIFI_IDENTITY = "wifi-identity"
    DEVELOPMENT_ACCESS = "development-access"
    RPI4_VIDEO = "rpi4-video"
    RPI4_UART3 = "rpi4-uart3"
    FFMPEG_LIBRARIES = "ffmpeg-libraries"
    RPI4_USB_HOST = "rpi4-usb-host"
    SCREEN_GRAPHICS_USB = "screen-graphics-usb"
    UART_DIAGNOSTICS = "uart-diagnostics"
    USB_DIAGNOSTICS = "usb-diagnostics"
    KERNEL_ENTRY_MARKER = "kernel-entry-marker"

    # Explicit capability restrictions.
    HEADLESS = "headless"
    NO_ETHERNET = "no-ethernet"
    NO_WIFI = "no-wifi"
    BLUETOOTH_UART_ROUTING = "bluetooth-uart-routing"
    USB_PERIPHERAL_ONLY = "usb-peripheral-only"
    NO_USB = "no-usb"
    NO_INPUT = "no-input"
    NO_32BIT = "no-32bit"
    NO_CONTAINERS = "no-containers"
    LOCAL_STORAGE_ONLY = "local-storage-only"

    # Independently selectable optimizations.
    KERNEL_SIZE = "kernel-size"
    TRIM_DEBUG_INSTRUMENTATION = "trim-debug-instrumentation"
    NO_CRON = "no-cron"
    SKIP_HAT_EEPROM = "skip-hat-eeprom"
    SKIP_POE_FAN = "skip-poe-fan"
    LZ4_KERNEL = "lz4-kernel"
    CUTDOWN_FIRMWARE = "cutdown-firmware"
    PREMERGE_OVERLAYS = "premerge-overlays"
    BOOT_RAMDISK = "boot-ramdisk"
    EEPROM_NO_NET_INSTALL = "eeprom-no-net-install"


BASE_GROUPS = (Group.MUSL_TOOLCHAIN, Group.EROFS_ROOT, Group.STATIC_CORE)
PROFILE_GROUPS = tuple(group for group in Group if group not in BASE_GROUPS)
