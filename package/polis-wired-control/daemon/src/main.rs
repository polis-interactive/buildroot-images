mod benchmark;
mod link;
mod logging;
mod service;
mod settings;
#[cfg(test)]
mod tests;
mod workflow;

use anyhow::{Result, ensure};
use clap::Parser;
use fs2::FileExt;
use kameo::actor::Spawn;
use polis_wired_host_api::{MAX_RPC, Request, RunStatus};
use service::{ApiCall, Runtime, Service};
use std::{
    fs::{File, OpenOptions},
    os::unix::fs::{DirBuilderExt, FileTypeExt, MetadataExt, OpenOptionsExt, PermissionsExt},
    path::{Path, PathBuf},
    sync::Arc,
};
use tokio::{
    io::{AsyncBufReadExt, AsyncReadExt, AsyncWriteExt, BufReader},
    net::UnixListener,
    sync::{Mutex, Semaphore, oneshot},
};

#[derive(Parser)]
#[command(
    about = "Own one explicitly configured Polis wired fixture; never resume interrupted writes"
)]
struct Args {}

fn process_lock(name: &str) -> Result<File> {
    let path = PathBuf::from(format!("/tmp/polis-wired-{name}.lock"));
    // O_NOFOLLOW, no truncation. These locks span different daemon state dirs.
    let file = OpenOptions::new()
        .read(true)
        .write(true)
        .create(true)
        .truncate(false)
        .mode(0o600)
        .custom_flags(0x20000)
        .open(path)?;
    let meta = file.metadata()?;
    ensure!(
        meta.is_file() && meta.nlink() == 1 && meta.uid() == std::fs::metadata("/proc/self")?.uid(),
        "unsafe daemon lock file"
    );
    file.try_lock_exclusive()
        .map_err(|e| anyhow::anyhow!("fixture already owned by another daemon: {e}"))?;
    Ok(file)
}
fn private_directory(path: &Path) -> Result<()> {
    if !path.exists() {
        std::fs::DirBuilder::new()
            .recursive(true)
            .mode(0o700)
            .create(path)?;
    }
    let meta = std::fs::symlink_metadata(path)?;
    ensure!(
        meta.is_dir()
            && meta.mode() & 0o077 == 0
            && meta.uid() == std::fs::metadata("/proc/self")?.uid(),
        "state directory must be owned by daemon user and mode 0700"
    );
    Ok(())
}

#[tokio::main]
async fn main() -> Result<()> {
    Args::parse();
    let config = settings::fixture();
    config.validate()?;
    ensure!(
        config.socket.parent() == Some(config.state_dir.as_path()),
        "socket must live directly in the private state directory"
    );
    private_directory(&config.state_dir)?;
    let _pico_lock = process_lock(&format!("pico-{}", config.pico_id.to_lowercase()))?;
    let _usb_lock = if !config.usb_path.is_empty() {
        Some(process_lock(&format!("usb-{}", config.usb_path))?)
    } else {
        None
    };
    let config = Arc::new(config);
    let current = config.state_dir.join("current.json");
    let mut status: RunStatus = if current.exists() {
        serde_json::from_slice(&tokio::fs::read(&current).await?)?
    } else {
        RunStatus::default()
    };
    if status.active {
        status.active = false;
        status.stage = "interrupted".into();
        status.failures.push(
            "Daemon stopped during operation; outcome uncertain. No automatic resume or replay."
                .into(),
        );
    }
    let state = Arc::new(Mutex::new(status));
    workflow::Journal {
        state: state.clone(),
        path: Some(current),
    }
    .save()
    .await?;
    let link = link::Link::start(config.clone())?;
    let mut connection = link.state.clone();
    tokio::spawn(async move {
        let mut previous = (false, None);
        let mut previous_counters = None;
        while connection.changed().await.is_ok() {
            let state = connection.borrow_and_update().clone();
            if let Some(status) = state.status {
                let counters = status.counters;
                let current = (
                    state.boot_id,
                    counters.uart_errors,
                    counters.uart_overruns,
                    counters.uart_rx_lost_bytes,
                );
                if previous_counters != Some(current)
                    && (current.1 != 0 || current.2 != 0 || current.3 != 0)
                {
                    logging::event(format!(
                        "[UART WARN] cumulative counters: errors={} overruns={} lost_bytes={} received_bytes={}",
                        counters.uart_errors,
                        counters.uart_overruns,
                        counters.uart_rx_lost_bytes,
                        status.uart_rx_offset
                    ));
                }
                previous_counters = Some(current);
            }
            let current = (state.connected, state.error.clone());
            if current != previous {
                if state.connected {
                    eprintln!("Pico connected; Hello verified, UART capture active");
                } else {
                    eprintln!(
                        "Pico disconnected: {}",
                        state.error.as_deref().unwrap_or("reconnecting")
                    );
                }
                previous = current;
            }
        }
    });
    let runtime = Arc::new(Runtime {
        config: config.clone(),
        link,
        operation: Arc::new(Mutex::new(())),
        status: state,
    });
    let actor = Service::spawn(Service(runtime));
    if config.socket.exists() {
        ensure!(
            std::fs::symlink_metadata(&config.socket)?
                .file_type()
                .is_socket(),
            "refusing to remove non-socket path"
        );
        ensure!(
            tokio::net::UnixStream::connect(&config.socket)
                .await
                .is_err(),
            "socket already in use"
        );
        tokio::fs::remove_file(&config.socket).await?;
    }
    let listener = UnixListener::bind(&config.socket)?;
    tokio::fs::set_permissions(&config.socket, std::fs::Permissions::from_mode(0o600)).await?;
    eprintln!(
        "Fixture {} listening on {} (Pi reflashing: {}); Ctrl-C to stop",
        config.fixture,
        config.socket.display(),
        if config.recovery_enabled {
            "configured"
        } else {
            "disabled"
        }
    );
    let permits = Arc::new(Semaphore::new(16));
    loop {
        tokio::select! {
            _ = tokio::signal::ctrl_c() => break,
            accepted = listener.accept() => {
                let (mut stream, _) = accepted?;
                let Ok(permit) = permits.clone().try_acquire_owned() else { continue; };
                let actor = actor.clone();
                tokio::spawn(async move {
                    let _permit = permit;
                    let result: Result<()> = async {
                        let mut line = String::new();
                        let read = tokio::time::timeout(std::time::Duration::from_secs(5), async {
                            BufReader::new(&mut stream).take(MAX_RPC as u64 + 1).read_line(&mut line).await
                        }).await??;
                        ensure!(read <= MAX_RPC && line.ends_with('\n'), "RPC frame too large or truncated");
                        let request: Request = serde_json::from_str(&line)?;
                        let (response, receiver) = oneshot::channel();
                        actor.tell(ApiCall { request, response }).await.map_err(|e| anyhow::anyhow!("service unavailable: {e}"))?;
                        let mut bytes = serde_json::to_vec(&receiver.await?)?; bytes.push(b'\n');
                        stream.write_all(&bytes).await?;
                        Ok(())
                    }.await;
                    if let Err(error) = result {
                        logging::event(format!("[RPC] connection/request error: {}", logging::text(format!("{error:#}").as_bytes())));
                        let response = polis_wired_host_api::Response::from_result(Err(error));
                        if let Ok(mut bytes) = serde_json::to_vec(&response) { bytes.push(b'\n'); let _ = stream.write_all(&bytes).await; }
                    }
                });
            }
        }
    }
    tokio::fs::remove_file(&config.socket).await?;
    Ok(())
}
