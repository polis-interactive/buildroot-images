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
    ETHERNET = "ethernet"
    DEVELOPMENT_SSH = "development-ssh"
    DISPLAY = "display"
    CAMERA = "camera"


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
    DEVELOPMENT_ACCESS = "development-access"
    UART_DIAGNOSTICS = "uart-diagnostics"
    USB_DIAGNOSTICS = "usb-diagnostics"
    KERNEL_ENTRY_MARKER = "kernel-entry-marker"

    # Explicit capability restrictions.
    HEADLESS = "headless"
    NO_ETHERNET = "no-ethernet"
    BLUETOOTH_UART_ROUTING = "bluetooth-uart-routing"
    USB_PERIPHERAL_ONLY = "usb-peripheral-only"
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
