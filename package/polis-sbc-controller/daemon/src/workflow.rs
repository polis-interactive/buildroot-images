use crate::config::Config;
use crate::link::Link;
use crate::storage_helper::StorageConfig;
use anyhow::{Result, bail, ensure};
use async_trait::async_trait;
use polis_sbc_controller_embedded_api::{BootMode, Command, Counters, Reply};
use polis_sbc_controller_host_api::{Image, RunStatus};
use serde_json::json;
use std::{path::PathBuf, sync::Arc};
use tokio::{
    io::AsyncWriteExt,
    process::Command as Process,
    sync::Mutex,
    time::{Duration, Instant, sleep},
};

#[async_trait]
pub trait Backend: Send + Sync {
    async fn preflight(&self, images: &[Image]) -> Result<()>;
    async fn recovery_boot(&self) -> Result<()>;
    async fn load_gadget(&self) -> Result<()>;
    async fn flash_verify(&self, image: &Image) -> Result<()>;
    async fn normal_boot(&self) -> Result<()>;
    /// Only a clean readiness deadline returns false. Transport and capture
    /// failures return Err and must not trigger an automatic destructive action.
    async fn ready(&self) -> Result<bool>;
}

pub struct Journal {
    pub state: Arc<Mutex<RunStatus>>,
    pub path: Option<PathBuf>,
}
impl Journal {
    pub async fn save(&self) -> Result<()> {
        if let Some(path) = &self.path {
            let bytes = serde_json::to_vec_pretty(&*self.state.lock().await)?;
            let temp = path.with_extension("tmp");
            let mut file = tokio::fs::File::create(&temp).await?;
            file.write_all(&bytes).await?;
            file.sync_all().await?;
            tokio::fs::rename(temp, path).await?;
            // Persist the rename, not just the new file's contents.
            std::fs::File::open(path.parent().unwrap())?.sync_all()?;
            let run_id = self.state.lock().await.run_id.clone();
            if !run_id.is_empty() {
                ensure!(
                    run_id
                        .bytes()
                        .all(|b| b.is_ascii_digit() || b.is_ascii_hexdigit() || b == b'-'),
                    "invalid journal run ID"
                );
                let archive = path.parent().unwrap().join("runs").join(run_id);
                tokio::fs::create_dir_all(&archive).await?;
                let mut file = tokio::fs::File::create(archive.join("status.json")).await?;
                file.write_all(&bytes).await?;
                file.sync_all().await?;
            }
        }
        Ok(())
    }
    pub async fn stage(&self, stage: &str) -> Result<()> {
        {
            let mut state = self.state.lock().await;
            state.stage = stage.into();
            state.steps.push(stage.into());
            crate::logging::event(format!("[workflow {}] stage={stage}", state.run_id));
        }
        self.save().await
    }
    pub async fn failure(&self, reason: String) -> Result<()> {
        self.state.lock().await.failures.push(reason);
        self.save().await
    }
}

async fn reflash(backend: &impl Backend, image: &Image, journal: &Journal) -> Result<()> {
    journal.stage("recovery_boot").await?;
    backend.recovery_boot().await?;
    journal.stage("load_ram_gadget").await?;
    backend.load_gadget().await?;
    journal.stage("flash_and_verify").await?;
    backend.flash_verify(image).await
}
async fn boot(backend: &impl Backend, journal: &Journal) -> Result<bool> {
    journal.stage("normal_boot").await?;
    backend.normal_boot().await?;
    journal.stage("wait_readiness").await?;
    backend.ready().await
}

pub async fn execute(
    backend: &impl Backend,
    image: Option<&Image>,
    recovery: Option<&Image>,
    auto: bool,
    journal: &Journal,
) -> Result<()> {
    ensure!(
        !auto || recovery.is_some(),
        "automatic recovery requires an explicitly configured known-good image"
    );
    let images: Vec<Image> = image
        .into_iter()
        .chain(recovery.filter(|_| auto))
        .cloned()
        .collect();
    journal.stage("preflight").await?;
    backend.preflight(&images).await?;
    if let Some(image) = image {
        reflash(backend, image, journal).await?;
    }
    if boot(backend, journal).await? {
        return journal.stage("ready").await;
    }
    journal
        .failure("Initial normal boot missed the readiness deadline; UART logs retained".into())
        .await?;
    if !auto {
        bail!("readiness deadline exceeded; automatic recovery is disabled");
    }
    journal.state.lock().await.recovery_attempts = 1;
    journal.save().await?;
    reflash(backend, recovery.unwrap(), journal).await?;
    if !boot(backend, journal).await? {
        bail!("known-good image also missed readiness; automatic recovery exhausted (1 attempt)");
    }
    journal.stage("recovered").await
}

pub struct RealBackend {
    pub config: Arc<Config>,
    pub link: Link,
    pub generation: u64,
    pub run_dir: PathBuf,
    readiness: Mutex<Option<(u64, u64, u64, Counters)>>,
}
impl RealBackend {
    pub fn new(config: Arc<Config>, link: Link, generation: u64, run_dir: PathBuf) -> Self {
        Self {
            config,
            link,
            generation,
            run_dir,
            readiness: Mutex::new(None),
        }
    }
    pub async fn commission_recovery_usb(&self, journal: &Journal) -> Result<()> {
        self.config.recovery.validate_loader()?;
        journal.stage("validate_ram_payload").await?;
        self.helper("payload", None, &[]).await?;
        journal.stage("recovery_boot").await?;
        self.pico(Command::SetPower { on: false }).await?;
        self.wait_probe("rom", false).await?;
        self.pico(Command::Boot {
            mode: BootMode::Recovery,
            off_ms: self.config.boot.off_ms,
        })
        .await?;
        self.wait_probe("rom", true).await?;
        journal.stage("rom_verified").await?;
        journal.stage("load_ram_gadget").await?;
        self.run_loader().await?;
        journal.stage("ram_payload_loaded").await
    }

    async fn run_loader(&self) -> Result<()> {
        let log = std::fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(self.run_dir.join("rpiboot.log"))?;
        let child = Process::new(&self.config.recovery.loader.rpiboot)
            .arg("-p")
            .arg(&self.config.recovery.usb_path)
            .arg("-d")
            .arg(&self.config.recovery.loader.gadget_dir)
            .stdout(log.try_clone()?)
            .stderr(log)
            .kill_on_drop(true)
            .spawn()?;
        let wait = child.wait_with_output();
        tokio::pin!(wait);
        let deadline = Instant::now() + Duration::from_secs(self.config.recovery.timeout_s);
        loop {
            tokio::select! {
                result = &mut wait => { ensure!(result?.status.success(), "rpiboot failed; see rpiboot.log"); break; },
                _ = sleep(Duration::from_millis(100)) => {
                    self.link.require_generation(self.generation)?;
                    ensure!(Instant::now() < deadline, "rpiboot deadline exceeded; no replay");
                }
            }
        }
        self.link.require_generation(self.generation)?;
        Ok(())
    }

    pub async fn pico(&self, command: Command) -> Result<Reply> {
        self.link.require_generation(self.generation)?;
        let reply = self.link.command(command).await?;
        self.link.require_generation(self.generation)?;
        if let Reply::Error(code) = reply {
            bail!("Pico rejected operation: {code:?}");
        }
        Ok(reply)
    }
    pub async fn helper(
        &self,
        action: &str,
        image: Option<&Image>,
        images: &[Image],
    ) -> Result<serde_json::Value> {
        self.link.require_generation(self.generation)?;
        let input = serde_json::to_vec(
            &json!({"action":action, "config":StorageConfig::from(self.config.as_ref()),
            "image":image, "images":images, "confirm_fixture":self.config.fixture}),
        )?;
        let mut command = Process::new("python3");
        command
            .args(["-m", "src.sbc_controller.storage"])
            .current_dir(&self.config.paths.repo_root)
            .stdin(std::process::Stdio::piped())
            .stdout(std::process::Stdio::piped())
            .stderr(std::process::Stdio::piped())
            .kill_on_drop(true);
        let mut child = command.spawn()?;
        child.stdin.take().unwrap().write_all(&input).await?;
        let wait = child.wait_with_output();
        tokio::pin!(wait);
        let deadline = Instant::now() + Duration::from_secs(self.config.recovery.flash_timeout_s);
        let output = loop {
            tokio::select! {
                result = &mut wait => break result?,
                _ = sleep(Duration::from_millis(100)) => {
                    self.link.require_generation(self.generation)?;
                    ensure!(Instant::now() < deadline, "storage adapter deadline exceeded; outcome uncertain, no replay");
                }
            }
        };
        self.link.require_generation(self.generation)?;
        ensure!(
            output.stdout.len() <= 1_048_576,
            "storage adapter response too large"
        );
        let response: serde_json::Value = serde_json::from_slice(&output.stdout).map_err(|e| {
            anyhow::anyhow!(
                "storage adapter: {e}; {}",
                String::from_utf8_lossy(&output.stderr)
            )
        })?;
        ensure!(
            output.status.success() && response["ok"] == true,
            "storage adapter: {}",
            response["error"]
        );
        Ok(response["data"].clone())
    }
    pub async fn capture_diagnostics(&self) -> Result<serde_json::Value> {
        self.link.require_generation(self.generation)?;
        ensure!(
            !self.config.recovery.usb_path.is_empty(),
            "explicit Pi USB topology required"
        );
        let mut child = Process::new("python3")
            .args(["-m", "src.sbc_controller.capture"])
            .current_dir(&self.config.paths.repo_root)
            .stdin(std::process::Stdio::piped())
            .stdout(std::process::Stdio::piped())
            .stderr(std::process::Stdio::piped())
            .kill_on_drop(true)
            .spawn()?;
        child
            .stdin
            .take()
            .unwrap()
            .write_all(&serde_json::to_vec(&json!({
                "usb_path":self.config.recovery.usb_path,
                "archive":self.config.paths.state_dir.join("captures")
            }))?)
            .await?;
        let wait = child.wait_with_output();
        tokio::pin!(wait);
        let deadline = Instant::now() + Duration::from_secs(40);
        let output = loop {
            tokio::select! {
                result = &mut wait => break result?,
                _ = sleep(Duration::from_millis(100)) => {
                    self.link.require_generation(self.generation)?;
                    ensure!(Instant::now() < deadline, "Pi diagnostic retrieval timed out");
                }
            }
        };
        self.link.require_generation(self.generation)?;
        let result: serde_json::Value =
            serde_json::from_slice(&output.stdout).context("invalid diagnostic helper response")?;
        ensure!(
            output.status.success() && result["ok"] == true,
            "Pi diagnostics: {}",
            result["error"]
        );
        let data = result["data"].clone();
        tokio::fs::write(
            self.run_dir.join("capture.json"),
            serde_json::to_vec_pretty(&data)?,
        )
        .await?;
        Ok(data)
    }

    async fn finish_diagnostics(&self, initial: Counters) -> Result<()> {
        let Reply::Status(status) = self.pico(Command::Status).await? else {
            bail!("expected diagnostic status");
        };
        self.link.sync_logs()?;
        let path = self.run_dir.join(format!(
            "boot-diagnostics-{:016x}.json",
            rand::random::<u64>()
        ));
        tokio::fs::write(path, serde_json::to_vec_pretty(&status)?).await?;
        validate_capture(initial, status.counters)
    }

    async fn wait_probe(&self, action: &str, present: bool) -> Result<()> {
        let deadline = Instant::now() + Duration::from_secs(self.config.recovery.timeout_s);
        loop {
            let value = self.helper(action, None, &[]).await?;
            let found = if action == "rom" {
                value == true
            } else {
                !value.is_null()
            };
            if found == present {
                return Ok(());
            }
            ensure!(
                Instant::now() < deadline,
                "{action} enumeration deadline exceeded"
            );
            sleep(Duration::from_millis(250)).await;
        }
    }
}
#[async_trait]
impl Backend for RealBackend {
    async fn preflight(&self, images: &[Image]) -> Result<()> {
        for image in images {
            image.validate()?;
        }
        if !images.is_empty() {
            self.config.recovery.validate()?;
            self.helper("preflight", None, images).await?;
        }
        self.pico(Command::Status).await?;
        Ok(())
    }
    async fn recovery_boot(&self) -> Result<()> {
        self.config.recovery.validate()?;
        ensure!(
            self.config.recovery.loader.otp_gpio8_provisioned,
            "GPIO8 OTP not qualified"
        );
        self.pico(Command::SetPower { on: false }).await?;
        self.wait_probe("probe", false).await?;
        self.pico(Command::Boot {
            mode: BootMode::Recovery,
            off_ms: self.config.boot.off_ms,
        })
        .await?;
        self.wait_probe("rom", true).await
    }
    async fn load_gadget(&self) -> Result<()> {
        self.helper("preflight", None, &[]).await?;
        self.run_loader().await?;
        self.wait_probe("probe", true).await
    }
    async fn flash_verify(&self, image: &Image) -> Result<()> {
        let result = self.helper("flash", Some(image), &[]).await?;
        let path = self
            .run_dir
            .join(format!("verify-{}.json", rand::random::<u64>()));
        tokio::fs::write(path, serde_json::to_vec_pretty(&result)?).await?;
        Ok(())
    }
    async fn normal_boot(&self) -> Result<()> {
        self.pico(Command::SetPower { on: false }).await?;
        let Reply::Status(status) = self.pico(Command::Status).await? else {
            bail!("expected Pico status");
        };
        let (cursor, gaps) = self.link.checkpoint(status.uptime_us);
        *self.readiness.lock().await = Some((cursor, status.uptime_us, gaps, status.counters));
        self.pico(Command::Boot {
            mode: BootMode::Normal,
            off_ms: self.config.boot.off_ms,
        })
        .await?;
        let Reply::Status(enabled) = self.pico(Command::Status).await? else {
            bail!("expected power-enable status");
        };
        tokio::fs::write(
            self.run_dir.join("power-enable.json"),
            serde_json::to_vec_pretty(&json!({
                "pico_boot_id":self.link.state.borrow().boot_id,
                "generation":self.generation,"before":status,"enabled":enabled
            }))?,
        )
        .await?;
        Ok(())
    }
    async fn ready(&self) -> Result<bool> {
        let (mut cursor, since_us, gaps, counters) = self
            .readiness
            .lock()
            .await
            .context("missing boot checkpoint")?;
        let needle = self.config.boot.readiness_marker.as_bytes();
        let provisioning_marker = b"POLIS_PROVISIONING ";
        let mut tail = Vec::new();
        let started = Instant::now();
        let mut deadline = started + Duration::from_secs(self.config.boot.readiness_timeout_s);
        let mut provisioning = false;
        loop {
            self.link.require_generation(self.generation)?;
            ensure!(
                self.link.gaps() == gaps,
                "UART capture lost data; readiness uncertain, no automatic flash"
            );
            let (next, bytes, lost) = self.link.read(cursor, 65536, since_us);
            ensure!(!lost, "UART capture buffer overrun; readiness uncertain");
            cursor = next;
            tail.extend_from_slice(&bytes);
            if !provisioning
                && tail
                    .windows(provisioning_marker.len())
                    .any(|w| w == provisioning_marker)
            {
                provisioning = true;
                deadline = started + Duration::from_secs(crate::settings::PROVISIONING_TIMEOUT_S);
                crate::logging::event("[provisioning] target setup observed; waiting up to 600 seconds without automatic reflash".into());
                tokio::fs::write(
                    self.run_dir.join("provisioning-observed.txt"),
                    "Target first-boot setup observed; separate from readiness and timing.\n",
                )
                .await?;
            }
            if tail.windows(needle.len()).any(|w| w == needle) {
                self.finish_diagnostics(counters).await?;
                if let Err(error) = self.capture_diagnostics().await {
                    let detail = format!("{error:#}");
                    tokio::fs::write(self.run_dir.join("capture-error.txt"), &detail).await?;
                    crate::logging::event(format!("[diagnostics] {detail}"));
                }
                self.link.require_generation(self.generation)?;
                return Ok(true);
            }
            let keep = needle.len().max(provisioning_marker.len());
            if tail.len() > keep {
                tail.drain(..tail.len() - keep);
            }
            if Instant::now() >= deadline {
                self.finish_diagnostics(counters).await?;
                ensure!(
                    !provisioning,
                    "provisioning readiness deadline exceeded; inspect setup status, no automatic reflash"
                );
                return Ok(false);
            }
            sleep(Duration::from_millis(50)).await;
        }
    }
}
use anyhow::Context;

pub(crate) fn validate_capture(before: Counters, after: Counters) -> Result<()> {
    ensure!(
        after.uart_errors == before.uart_errors
            && after.uart_overruns == before.uart_overruns
            && after.uart_rx_lost_bytes == before.uart_rx_lost_bytes
            && after.usb_tx_failures == before.usb_tx_failures,
        "Pico UART/USB capture lost data; boot outcome uncertain, no automatic reflash"
    );
    Ok(())
}
