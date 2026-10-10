use anyhow::{Result, bail, ensure};
use serde::{Deserialize, Serialize};
use std::path::{Path, PathBuf};
use tokio::{
    io::{AsyncBufReadExt, AsyncReadExt, AsyncWriteExt, BufReader},
    net::UnixStream,
};

pub const API_VERSION: u16 = 1;
pub const MAX_RPC: usize = 65_536;

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Image {
    pub path: PathBuf,
    pub sha256: String,
}

#[derive(Clone, Debug, Serialize)]
pub struct Config {
    pub fixture: String,
    pub pico_path: PathBuf,
    pub pico_id: String,
    pub recovery_enabled: bool,
    pub usb_path: String,
    pub disk_id_path: String,
    pub disk_serial: String,
    pub gadget_dir: PathBuf,
    pub gadget_sha256: String,
    pub rpiboot: PathBuf,
    pub repo_root: PathBuf,
    pub state_dir: PathBuf,
    pub socket: PathBuf,
    pub otp_gpio8_provisioned: bool,
    pub recovery_image: Option<Image>,
    pub readiness_marker: String,
    pub readiness_timeout_s: u64,
    pub recovery_timeout_s: u64,
    pub flash_timeout_s: u64,
    pub off_ms: u32,
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
        ensure!(
            self.pico_id.len() == 16 && self.pico_id.bytes().all(|c| c.is_ascii_hexdigit()),
            "pico_id must be its 16-digit chip ID"
        );
        if self.recovery_enabled {
            self.validate_recovery()?;
        }
        ensure!(
            !self.readiness_marker.is_empty() && self.readiness_marker.len() <= 256,
            "readiness marker must contain 1..256 bytes"
        );
        ensure!(
            (1..=3600).contains(&self.readiness_timeout_s)
                && (1..=600).contains(&self.recovery_timeout_s)
                && (1..=86400).contains(&self.flash_timeout_s),
            "deadlines out of bounds"
        );
        ensure!((100..=60000).contains(&self.off_ms), "off_ms out of bounds");
        for path in [
            &self.pico_path,
            &self.repo_root,
            &self.state_dir,
            &self.socket,
        ] {
            ensure!(
                path.is_absolute(),
                "configuration paths must be absolute: {}",
                path.display()
            );
        }
        Ok(())
    }

    pub fn validate_recovery(&self) -> Result<()> {
        ensure!(
            self.recovery_enabled,
            "Pi reflashing is disabled; configure recovery before flashing or enabling auto-recovery"
        );
        self.validate_recovery_loader()?;
        ensure!(
            !self.disk_id_path.is_empty() && !self.disk_serial.is_empty(),
            "explicit SD gadget ID_PATH and ID_SERIAL_SHORT required"
        );
        if let Some(image) = &self.recovery_image {
            image.validate()?;
        }
        Ok(())
    }

    /// Commission the RAM loader before a recovery disk identity is known.
    pub fn validate_recovery_loader(&self) -> Result<()> {
        ensure!(self.otp_gpio8_provisioned, "GPIO8 OTP not qualified");
        ensure!(
            self.usb_path.contains('-')
                && self
                    .usb_path
                    .bytes()
                    .all(|c| c.is_ascii_digit() || b"-.".contains(&c)),
            "usb_path must be an explicit bus-port topology, e.g. 1-2.3"
        );
        for path in [&self.gadget_dir, &self.rpiboot] {
            ensure!(
                path.is_absolute(),
                "configuration paths must be absolute: {}",
                path.display()
            );
        }
        validate_hash(&self.gadget_sha256)?;
        Ok(())
    }
}
impl Image {
    pub fn validate(&self) -> Result<()> {
        ensure!(self.path.is_absolute(), "image path must be absolute");
        validate_hash(&self.sha256)
    }
}
fn validate_hash(value: &str) -> Result<()> {
    ensure!(
        value.len() == 64 && value.bytes().all(|c| c.is_ascii_hexdigit()),
        "SHA-256 must have 64 hexadecimal digits"
    );
    Ok(())
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(tag = "action", rename_all = "snake_case", deny_unknown_fields)]
pub enum Action {
    Status,
    Images,
    Diagnostics,
    Benchmark {
        samples: u32,
        confirm_fixture: String,
    },
    RecoveryUsb {
        confirm_fixture: String,
    },
    ConsoleRead {
        after: u64,
        max_bytes: usize,
    },
    Power {
        on: bool,
        confirm_fixture: String,
    },
    Cycle {
        off_ms: u32,
        confirm_fixture: String,
    },
    ConsoleWrite {
        text: String,
    },
    Workflow {
        image: Option<Image>,
        auto_recover: bool,
        confirm_fixture: String,
    },
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Request {
    pub api_version: u16,
    pub fixture: String,
    #[serde(rename = "operation")]
    pub action: Action,
}
#[derive(Debug, Serialize, Deserialize)]
pub struct Response {
    pub ok: bool,
    pub data: serde_json::Value,
    pub error: Option<String>,
}
impl Response {
    pub fn from_result(result: Result<serde_json::Value>) -> Self {
        match result {
            Ok(data) => Self {
                ok: true,
                data,
                error: None,
            },
            Err(error) => Self {
                ok: false,
                data: serde_json::Value::Null,
                error: Some(format!("{error:#}")),
            },
        }
    }
}

#[derive(Clone, Debug, Default, Serialize, Deserialize)]
pub struct RunStatus {
    pub initial_image: Option<Image>,
    pub recovery_image: Option<Image>,
    pub auto_recover: bool,
    pub run_id: String,
    pub active: bool,
    pub stage: String,
    pub recovery_attempts: u8,
    pub failures: Vec<String>,
    pub uart_start: u64,
    pub uart_end: u64,
    pub steps: Vec<String>,
}

pub async fn call(socket: &Path, request: Request) -> Result<serde_json::Value> {
    tokio::time::timeout(
        std::time::Duration::from_secs(75),
        call_inner(socket, request),
    )
    .await
    .map_err(|_| {
        anyhow::anyhow!("daemon reply deadline exceeded; outcome uncertain, do not replay")
    })?
}

async fn call_inner(socket: &Path, request: Request) -> Result<serde_json::Value> {
    let mut stream = UnixStream::connect(socket).await?;
    let mut wire = serde_json::to_vec(&request)?;
    ensure!(wire.len() < MAX_RPC, "request too large");
    wire.push(b'\n');
    stream.write_all(&wire).await?;
    let mut line = String::new();
    BufReader::new(stream)
        .take(MAX_RPC as u64 + 1)
        .read_line(&mut line)
        .await?;
    ensure!(
        line.len() <= MAX_RPC && line.ends_with('\n'),
        "invalid/truncated daemon response"
    );
    let response: Response = serde_json::from_str(&line)?;
    if !response.ok {
        bail!(
            "{}",
            response.error.unwrap_or_else(|| "daemon error".into())
        );
    }
    Ok(response.data)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn cli_mcp_and_daemon_share_the_same_json_contract() {
        for action in [
            Action::Status,
            Action::Workflow {
                image: Some(Image {
                    path: "/tmp/image.img".into(),
                    sha256: "a".repeat(64),
                }),
                auto_recover: true,
                confirm_fixture: "bench".into(),
            },
            Action::ConsoleRead {
                after: 42,
                max_bytes: 4096,
            },
        ] {
            let request = Request {
                api_version: API_VERSION,
                fixture: "bench".into(),
                action,
            };
            let encoded = serde_json::to_vec(&request).unwrap();
            let decoded: Request = serde_json::from_slice(&encoded).unwrap();
            assert_eq!(
                serde_json::to_value(decoded).unwrap(),
                serde_json::to_value(request).unwrap()
            );
        }
    }
    #[test]
    fn image_requires_absolute_path_and_pinned_hash() {
        assert!(
            Image {
                path: "relative.img".into(),
                sha256: "a".repeat(64)
            }
            .validate()
            .is_err()
        );
        assert!(
            Image {
                path: "/image.img".into(),
                sha256: String::new()
            }
            .validate()
            .is_err()
        );
    }
}
