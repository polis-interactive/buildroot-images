//! Fixture settings live here, in source. No runtime configuration files.
use polis_wired_host_api::{Config, Image};
use std::path::PathBuf;

pub const FIXTURE: &str = "bench-rpi4b";
pub const PICO_ID: &str = "4c5a203268260e2c";
pub const PICO_PATH: &str = "/dev/serial/by-id/usb-Polis_Polis_Wired_Control_4c5a203268260e2c-if00";

// Relative to the repository root. This directory contains logs and journals,
// not configuration. The build embeds the repository's absolute location.
pub const STATE_DIR: &str = "local/polis-wired-state";
pub const SOCKET_NAME: &str = "daemon.sock";
pub const READINESS_MARKER: &str = "POLIS_SMOKE_READY init";
pub const READINESS_TIMEOUT_S: u64 = 60;
pub const RECOVERY_TIMEOUT_S: u64 = 60;
pub const FLASH_TIMEOUT_S: u64 = 1800;
pub const POWER_OFF_MS: u32 = 1000;
pub const CONSOLE_LOG_QUEUE: usize = 256;
pub const UART_CONSOLE_LINE_BYTES: usize = 1024;
pub const UART_CONSOLE_IDLE_MS: u64 = 500;

// Power/UART work without Pi USB recovery. Fill in these values only after
// qualifying the recovery hardware, then explicitly enable it here.
pub const RECOVERY_ENABLED: bool = true;
// Provisioning UART update + repeated green LED confirmed 2026-10-09.
pub const OTP_GPIO8_PROVISIONED: bool = true;
// Hardware-observed ROM and RAM recovery gadget on this dedicated USB port.
pub const PI_USB_PATH: &str = "3-2";
pub const SD_DISK_ID_PATH: &str = "pci-0000:00:14.0-usb-0:2:1.0-scsi-0:0:0:0";
pub const SD_DISK_SERIAL: &str = "1000000000e4bb49";
// Official usbboot commit 861a703d8b51f42b76bcdff49da3e0f2766966bf.
// Paths are relative to the repository, resolved below before use.
pub const GADGET_DIR: &str = "local/polis-wired-tools/gadget";
pub const GADGET_SHA256: &str = include_str!("../../recovery/payload.sha256");
pub const RPIBOOT: &str = "local/usbboot/rpiboot";
// Explicit known-good image (path relative to repository); never auto-select.
// Normal boot/USB and recovery write/readback verified on this bench.
pub const RECOVERY_IMAGE: Option<(&str, &str)> = Some((
    "snapshots/polis_rpi4_musl_erofs-well-known/images/sdcard.img",
    "847af678eb5e732c61322d64d2c7362489f7f4b58b1a4b886211b14dd5acece3",
));

pub fn fixture() -> Config {
    let repo_root = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .ancestors()
        .nth(3)
        .expect("daemon must live under package/polis-wired-control")
        .to_path_buf();
    let state_dir = repo_root.join(STATE_DIR);
    let recovery_image = RECOVERY_IMAGE.map(|(path, sha256)| Image {
        path: repo_root.join(path),
        sha256: sha256.into(),
    });
    Config {
        fixture: FIXTURE.into(),
        pico_path: PICO_PATH.into(),
        pico_id: PICO_ID.into(),
        recovery_enabled: RECOVERY_ENABLED,
        usb_path: PI_USB_PATH.into(),
        disk_id_path: SD_DISK_ID_PATH.into(),
        disk_serial: SD_DISK_SERIAL.into(),
        gadget_dir: repo_root.join(GADGET_DIR),
        gadget_sha256: GADGET_SHA256.trim().into(),
        rpiboot: repo_root.join(RPIBOOT),
        repo_root,
        socket: state_dir.join(SOCKET_NAME),
        state_dir,
        otp_gpio8_provisioned: OTP_GPIO8_PROVISIONED,
        recovery_image,
        readiness_marker: READINESS_MARKER.into(),
        readiness_timeout_s: READINESS_TIMEOUT_S,
        recovery_timeout_s: RECOVERY_TIMEOUT_S,
        flash_timeout_s: FLASH_TIMEOUT_S,
        off_ms: POWER_OFF_MS,
    }
}
