use crate::{
    link::Link,
    workflow::{self, Journal, RealBackend},
};
use anyhow::{Result, ensure};
use kameo::{
    Actor,
    message::{Context, Message},
};
use polis_wired_host_api::{API_VERSION, Action, Config, Request, Response, RunStatus};
use polis_wired_protocol::{Bytes, Command, Reply};
use serde_json::{Value, json};
use std::{
    sync::Arc,
    time::{SystemTime, UNIX_EPOCH},
};
use tokio::sync::{Mutex, oneshot};

pub struct Runtime {
    pub config: Arc<Config>,
    pub link: Link,
    pub operation: Arc<Mutex<()>>,
    pub status: Arc<Mutex<RunStatus>>,
}
impl Runtime {
    pub async fn dispatch(self: &Arc<Self>, request: Request) -> Result<Value> {
        let log = crate::logging::RequestLog::received(&request.fixture, &request.action);
        let result = self.dispatch_inner(request).await;
        log.finished(&result);
        result
    }
    async fn dispatch_inner(self: &Arc<Self>, request: Request) -> Result<Value> {
        ensure!(
            request.api_version == API_VERSION,
            "unsupported daemon API version"
        );
        ensure!(
            request.fixture == self.config.fixture,
            "fixture must match the daemon's explicit selection"
        );
        match request.action {
            Action::Status => {
                let workflow = self.status.lock().await.clone();
                let run_dir = self.config.state_dir.join("runs").join(&workflow.run_id);
                let benchmark = tokio::fs::read(run_dir.join("benchmark.json"))
                    .await
                    .ok()
                    .and_then(|bytes| serde_json::from_slice::<Value>(&bytes).ok())
                    .map(|mut value| {
                        if let Some(object) = value.as_object_mut() {
                            object.remove("samples");
                        }
                        value
                    });
                Ok(
                    json!({"fixture":self.config.fixture,"recovery_enabled":self.config.recovery_enabled,
                    "connection":self.link.state.borrow().clone(),"workflow":workflow,
                    "uart_log":self.config.state_dir.join("uart.bin"),"run_dir":run_dir,
                    "capture_archive":self.config.state_dir.join("captures"),"benchmark":benchmark}),
                )
            }
            Action::ConsoleRead { after, max_bytes } => {
                ensure!((1..=4096).contains(&max_bytes), "max_bytes must be 1..4096");
                let (next, bytes, gap) = self.link.read(after, max_bytes, 0);
                Ok(
                    json!({"next":next,"bytes":bytes,"text":String::from_utf8_lossy(&bytes),"gap":gap}),
                )
            }
            Action::Diagnostics => {
                let _guard = self.acquire_operation().await?;
                let backend = self.backend("diagnostics")?;
                tokio::fs::create_dir_all(&backend.run_dir).await?;
                backend.capture_diagnostics().await
            }
            Action::Images => {
                use tokio::io::AsyncWriteExt;
                let mut child = tokio::process::Command::new("python3")
                    .args(["-m", "src.wired.storage"])
                    .current_dir(&self.config.repo_root)
                    .stdin(std::process::Stdio::piped())
                    .stdout(std::process::Stdio::piped())
                    .kill_on_drop(true)
                    .spawn()?;
                child
                    .stdin
                    .take()
                    .unwrap()
                    .write_all(&serde_json::to_vec(
                        &json!({"action":"images","config":*self.config}),
                    )?)
                    .await?;
                let output = tokio::time::timeout(
                    std::time::Duration::from_secs(20),
                    child.wait_with_output(),
                )
                .await??;
                let result: Value = serde_json::from_slice(&output.stdout)?;
                ensure!(
                    output.status.success() && result["ok"] == true,
                    "image discovery failed: {}",
                    result["error"]
                );
                Ok(result["data"].clone())
            }
            Action::Power {
                on,
                confirm_fixture,
            } => {
                self.confirm(&confirm_fixture)?;
                let _guard = self.acquire_operation().await?;
                self.command(Command::SetPower { on }).await
            }
            Action::Cycle {
                off_ms,
                confirm_fixture,
            } => {
                self.confirm(&confirm_fixture)?;
                ensure!((100..=60000).contains(&off_ms), "off_ms out of bounds");
                let _guard = self.acquire_operation().await?;
                self.command(Command::CyclePower { off_ms }).await
            }
            Action::ConsoleWrite { text } => {
                ensure!(!text.is_empty(), "empty UART write");
                let bytes = Bytes::from_slice(text.as_bytes())
                    .map_err(|_| anyhow::anyhow!("UART write exceeds 128 bytes"))?;
                let _guard = self.acquire_operation().await?;
                self.command(Command::WriteUart { bytes }).await
            }
            action @ (Action::Workflow { .. }
            | Action::RecoveryUsb { .. }
            | Action::Benchmark { .. }) => {
                let (image, auto_recover, confirm_fixture, commissioning, samples) = match action {
                    Action::Workflow {
                        image,
                        auto_recover,
                        confirm_fixture,
                    } => (image, auto_recover, confirm_fixture, false, 0),
                    Action::RecoveryUsb { confirm_fixture } => {
                        (None, false, confirm_fixture, true, 0)
                    }
                    Action::Benchmark {
                        samples,
                        confirm_fixture,
                    } => {
                        ensure!((1..=20).contains(&samples), "samples must be 1..20");
                        (None, false, confirm_fixture, false, samples)
                    }
                    _ => unreachable!(),
                };
                self.confirm(&confirm_fixture)?;
                if commissioning {
                    self.config.validate_recovery_loader()?;
                }
                if image.is_some() || auto_recover {
                    self.config.validate_recovery()?;
                }
                ensure!(
                    !auto_recover || self.config.recovery_image.is_some(),
                    "automatic recovery requires a configured known-good image"
                );
                if let Some(image) = &image {
                    image.validate()?;
                }
                let guard = self.acquire_operation().await?;
                let run_id = format!(
                    "{}-{:016x}",
                    SystemTime::now().duration_since(UNIX_EPOCH)?.as_millis(),
                    rand::random::<u64>()
                );
                let backend = self.backend(&run_id)?;
                tokio::fs::create_dir_all(&backend.run_dir).await?;
                let plan = json!({"config":*self.config,"initial_image":image,"auto_recover":auto_recover,"commissioning":commissioning,"benchmark_samples":samples});
                let mut plan_file =
                    tokio::fs::File::create(backend.run_dir.join("plan.json")).await?;
                {
                    use tokio::io::AsyncWriteExt;
                    plan_file
                        .write_all(&serde_json::to_vec_pretty(&plan)?)
                        .await?;
                }
                plan_file.sync_all().await?;
                *self.status.lock().await = RunStatus {
                    initial_image: image.clone(),
                    recovery_image: self.config.recovery_image.clone(),
                    auto_recover,
                    run_id: run_id.clone(),
                    active: true,
                    uart_start: self.link.cursor(),
                    stage: "starting".into(),
                    ..Default::default()
                };
                let journal = Journal {
                    state: self.status.clone(),
                    path: Some(self.config.state_dir.join("current.json")),
                };
                journal.save().await?;
                let runtime = self.clone();
                tokio::spawn(async move {
                    let _guard = guard;
                    let result = if samples > 0 {
                        crate::benchmark::execute(&backend, samples, &journal).await
                    } else if commissioning {
                        backend.commission_recovery_usb(&journal).await
                    } else {
                        workflow::execute(
                            &backend,
                            image.as_ref(),
                            runtime.config.recovery_image.as_ref(),
                            auto_recover,
                            &journal,
                        )
                        .await
                    };
                    let log_result = runtime.link.sync_logs();
                    {
                        let mut state = runtime.status.lock().await;
                        if let Err(error) = result.and(log_result) {
                            state.failures.push(format!("{error:#}"));
                            state.stage = "failed".into();
                        }
                        state.active = false;
                        state.uart_end = runtime.link.cursor();
                        crate::logging::event(format!(
                            "[workflow {}] finished stage={} failures={}",
                            state.run_id,
                            state.stage,
                            state.failures.len()
                        ));
                        if let Some(error) = state.failures.last() {
                            crate::logging::event(format!(
                                "[workflow {}] {}",
                                state.run_id,
                                crate::logging::text(error.as_bytes())
                            ));
                        }
                    }
                    if let Err(error) = journal.save().await {
                        eprintln!("Failed to persist final workflow status: {error:#}");
                    }
                });
                Ok(
                    json!({"run_id":run_id,"started":true,"run_dir":self.config.state_dir.join("runs").join(&run_id)}),
                )
            }
        }
    }
    async fn acquire_operation(&self) -> Result<tokio::sync::OwnedMutexGuard<()>> {
        let guard = self
            .operation
            .clone()
            .try_lock_owned()
            .map_err(|_| anyhow::anyhow!("fixture busy"))?;
        ensure!(
            !self.status.lock().await.active,
            "previous workflow did not finish; inspect journal before restarting the daemon"
        );
        Ok(guard)
    }
    fn confirm(&self, name: &str) -> Result<()> {
        ensure!(
            name == self.config.fixture,
            "confirm_fixture must name the selected fixture"
        );
        Ok(())
    }
    fn backend(&self, run_id: &str) -> Result<RealBackend> {
        Ok(RealBackend::new(
            self.config.clone(),
            self.link.clone(),
            self.link.generation()?,
            self.config.state_dir.join("runs").join(run_id),
        ))
    }
    async fn command(&self, command: Command) -> Result<Value> {
        let result = self.link.command(command).await?;
        ensure!(
            !matches!(result, Reply::Error(_)),
            "Pico rejected command: {result:?}"
        );
        Ok(serde_json::to_value(result)?)
    }
}

#[derive(Actor)]
pub struct Service(pub Arc<Runtime>);
pub struct ApiCall {
    pub request: Request,
    pub response: oneshot::Sender<Response>,
}
impl Message<ApiCall> for Service {
    type Reply = ();
    async fn handle(&mut self, call: ApiCall, _: &mut Context<Self, Self::Reply>) {
        let runtime = self.0.clone();
        tokio::spawn(async move {
            let result = runtime.dispatch(call.request).await;
            let _ = call.response.send(Response::from_result(result));
        });
    }
}
