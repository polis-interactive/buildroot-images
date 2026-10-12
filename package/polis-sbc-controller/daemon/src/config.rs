//! Daemon-owned fixture configuration. Settings supplies values; this module owns
//! validation. Configuration is serialized flat in plan.json audit records.
use anyhow::{Result, ensure};
use polis_sbc_controller_host_api::Image;
use serde::Serialize;
use std::path::{Path, PathBuf};

#[derive(Clone, Debug, Serialize)]
pub struct Config {
    pub fixture: String,
    #[serde(flatten)]
    pub paths: Paths,
    #[serde(flatten)]
    pub boot: BootConfig,
    #[serde(flatten)]
    pub recovery: RecoveryConfig,
}

#[derive(Clone, Debug, Serialize)]
pub struct Paths {
    pub repo_root: PathBuf,
    pub state_dir: PathBuf,
    pub socket: PathBuf,
}

#[derive(Clone, Debug, Serialize)]
pub struct BootConfig {
    pub readiness_marker: String,
    pub readiness_timeout_s: u64,
    pub off_ms: u32,
}

#[derive(Clone, Debug, Serialize)]
pub struct RecoveryConfig {
    #[serde(rename = "recovery_enabled")]
    pub enabled: bool,
    pub usb_path: String,
    #[serde(flatten)]
    pub loader: RecoveryLoader,
    #[serde(flatten)]
    pub disk: DiskIdentity,
    #[serde(rename = "recovery_image")]
    pub image: Option<Image>,
    #[serde(rename = "recovery_timeout_s")]
    pub timeout_s: u64,
    pub flash_timeout_s: u64,
}

#[derive(Clone, Debug, Serialize)]
pub struct RecoveryLoader {
    pub gadget_dir: PathBuf,
    pub gadget_sha256: String,
    pub rpiboot: PathBuf,
    pub otp_gpio8_provisioned: bool,
}

#[derive(Clone, Debug, Serialize)]
pub struct DiskIdentity {
    #[serde(rename = "disk_id_path")]
    pub id_path: String,
    #[serde(rename = "disk_serial")]
    pub serial: String,
}

impl Config {
    pub fn validate(&self) -> Result<()> {
        ensure!(
            !self.fixture.is_empty()
                && self
                    .fixture
                    .bytes()
                    .all(|c| c.is_ascii_alphanumeric() || b"-_".contains(&c)),
            "fixture must be a nonempty simple name"
        );
        self.paths.validate()?;
        self.boot.validate()?;
        ensure!(
            (1..=600).contains(&self.recovery.timeout_s)
                && (1..=86400).contains(&self.recovery.flash_timeout_s),
            "deadlines out of bounds"
        );
        if self.recovery.enabled {
            self.recovery.validate()?;
        }
        Ok(())
    }
}
impl Paths {
    fn validate(&self) -> Result<()> {
        for path in [&self.repo_root, &self.state_dir, &self.socket] {
            absolute(path)?;
        }
        ensure!(
            self.socket.parent() == Some(self.state_dir.as_path()),
            "socket must live directly in the private state directory"
        );
        Ok(())
    }
}
impl BootConfig {
    fn validate(&self) -> Result<()> {
        ensure!(
            !self.readiness_marker.is_empty() && self.readiness_marker.len() <= 256,
            "readiness marker must contain 1..256 bytes"
        );
        ensure!(
            (1..=3600).contains(&self.readiness_timeout_s),
            "deadlines out of bounds"
        );
        ensure!((100..=60000).contains(&self.off_ms), "off_ms out of bounds");
        Ok(())
    }
}
impl RecoveryConfig {
    pub fn validate(&self) -> Result<()> {
        ensure!(
            self.enabled,
            "Pi reflashing is disabled; configure recovery before flashing or enabling auto-recovery"
        );
        self.validate_loader()?;
        ensure!(
            !self.disk.id_path.is_empty() && !self.disk.serial.is_empty(),
            "explicit SD gadget ID_PATH and ID_SERIAL_SHORT required"
        );
        if let Some(image) = &self.image {
            image.validate()?;
        }
        Ok(())
    }

    /// RAM-only commissioning does not require flash permission or a disk identity.
    pub fn validate_loader(&self) -> Result<()> {
        ensure!(self.loader.otp_gpio8_provisioned, "GPIO8 OTP not qualified");
        ensure!(
            self.usb_path.contains('-')
                && self
                    .usb_path
                    .bytes()
                    .all(|c| c.is_ascii_digit() || b"-.".contains(&c)),
            "usb_path must be an explicit bus-port topology, e.g. 1-2.3"
        );
        absolute(&self.loader.gadget_dir)?;
        absolute(&self.loader.rpiboot)?;
        ensure!(
            self.loader.gadget_sha256.len() == 64
                && self
                    .loader
                    .gadget_sha256
                    .bytes()
                    .all(|c| c.is_ascii_hexdigit()),
            "SHA-256 must have 64 hexadecimal digits"
        );
        Ok(())
    }
}
fn absolute(path: &Path) -> Result<()> {
    ensure!(
        path.is_absolute(),
        "configuration paths must be absolute: {}",
        path.display()
    );
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn startup_rejects_invalid_identity_paths_and_deadlines() {
        let valid = crate::settings::fixture();
        valid.validate().unwrap();
        let invalid: [fn(&mut Config); 8] = [
            |c| c.fixture = "../other".into(),
            |c| c.paths.repo_root = "relative-repo".into(),
            |c| c.paths.socket = c.paths.state_dir.join("nested/daemon.sock"),
            |c| c.boot.readiness_marker.clear(),
            |c| c.boot.readiness_timeout_s = 0,
            |c| c.boot.off_ms = 99,
            |c| c.recovery.timeout_s = 601,
            |c| c.recovery.flash_timeout_s = 86_401,
        ];
        for mutate in invalid {
            let mut config = valid.clone();
            mutate(&mut config);
            assert!(
                config.validate().is_err(),
                "accepted invalid config: {config:?}"
            );
        }
    }

    #[test]
    fn audit_plan_keeps_configuration_flat_without_a_pinned_pico() {
        let mut config = crate::settings::fixture();
        config.recovery.disk.id_path = "disk-path".into();
        config.recovery.disk.serial = "disk-serial".into();
        config.recovery.enabled = false;
        config.recovery.image = None;
        config.recovery.timeout_s = 42;
        let value = serde_json::to_value(config).unwrap();
        let mut expected = [
            "fixture",
            "recovery_enabled",
            "usb_path",
            "disk_id_path",
            "disk_serial",
            "gadget_dir",
            "gadget_sha256",
            "rpiboot",
            "repo_root",
            "state_dir",
            "socket",
            "otp_gpio8_provisioned",
            "recovery_image",
            "readiness_marker",
            "readiness_timeout_s",
            "recovery_timeout_s",
            "flash_timeout_s",
            "off_ms",
        ];
        expected.sort();
        let mut actual: Vec<_> = value
            .as_object()
            .unwrap()
            .keys()
            .map(String::as_str)
            .collect();
        actual.sort();
        assert_eq!(actual, expected);
        assert_eq!(value["disk_id_path"], "disk-path");
        assert_eq!(value["disk_serial"], "disk-serial");
        assert_eq!(value["recovery_enabled"], false);
        assert_eq!(value["recovery_image"], serde_json::Value::Null);
        assert_eq!(value["recovery_timeout_s"], 42);
    }
}
