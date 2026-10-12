//! Explicit configuration contract with src.sbc_controller.storage. Do not serialize the
//! entire daemon configuration into subprocess requests.
use crate::config::Config;
use serde::Serialize;
use std::path::Path;

#[derive(Serialize)]
pub struct StorageConfig<'a> {
    fixture: &'a str,
    repo_root: &'a Path,
    usb_path: &'a str,
    disk_id_path: &'a str,
    disk_serial: &'a str,
    gadget_dir: &'a Path,
    gadget_sha256: &'a str,
    otp_gpio8_provisioned: bool,
}
impl<'a> From<&'a Config> for StorageConfig<'a> {
    fn from(config: &'a Config) -> Self {
        Self {
            fixture: &config.fixture,
            repo_root: &config.paths.repo_root,
            usb_path: &config.recovery.usb_path,
            disk_id_path: &config.recovery.disk.id_path,
            disk_serial: &config.recovery.disk.serial,
            gadget_dir: &config.recovery.loader.gadget_dir,
            gadget_sha256: &config.recovery.loader.gadget_sha256,
            otp_gpio8_provisioned: config.recovery.loader.otp_gpio8_provisioned,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn python_payload_contains_only_the_storage_contract() {
        let mut config = crate::settings::fixture();
        config.fixture = "test-bench".into();
        config.paths.repo_root = "/repo".into();
        config.recovery.usb_path = "1-2.3".into();
        config.recovery.disk.id_path = "disk-path".into();
        config.recovery.disk.serial = "disk-serial".into();
        config.recovery.loader.gadget_dir = "/payload".into();
        config.recovery.loader.gadget_sha256 = "a".repeat(64);
        config.recovery.loader.otp_gpio8_provisioned = true;
        assert_eq!(
            serde_json::to_value(StorageConfig::from(&config)).unwrap(),
            serde_json::json!({
                "fixture": "test-bench", "repo_root": "/repo", "usb_path": "1-2.3",
                "disk_id_path": "disk-path", "disk_serial": "disk-serial",
                "gadget_dir": "/payload", "gadget_sha256": "a".repeat(64),
                "otp_gpio8_provisioned": true,
            })
        );
    }
}
