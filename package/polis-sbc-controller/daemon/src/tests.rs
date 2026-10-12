use super::workflow::{Backend, Journal, execute};
use anyhow::{Result, bail};
use async_trait::async_trait;
use polis_sbc_controller_host_api::{Image, RunStatus};
use std::{
    collections::VecDeque,
    sync::{Arc, Mutex},
};

struct Sim {
    steps: Mutex<Vec<String>>,
    ready: Mutex<VecDeque<bool>>,
    fail: Option<&'static str>,
}
impl Sim {
    fn new(ready: &[bool], fail: Option<&'static str>) -> Self {
        Self {
            steps: Mutex::new(Vec::new()),
            ready: Mutex::new(ready.iter().copied().collect()),
            fail,
        }
    }
    fn step(&self, value: &str) -> Result<()> {
        self.steps.lock().unwrap().push(value.into());
        if self.fail == Some(value) {
            bail!("simulated {value} failure; ambiguous or unsafe to retry");
        }
        Ok(())
    }
}
#[async_trait]
impl Backend for Sim {
    async fn preflight(&self, _: &[Image]) -> Result<()> {
        self.step("preflight")
    }
    async fn recovery_boot(&self) -> Result<()> {
        self.step("recovery_boot")
    }
    async fn load_gadget(&self) -> Result<()> {
        self.step("gadget")
    }
    async fn flash_verify(&self, _: &Image) -> Result<()> {
        self.step("flash_verify")
    }
    async fn normal_boot(&self) -> Result<()> {
        self.step("normal_boot")
    }
    async fn ready(&self) -> Result<bool> {
        self.step("ready")?;
        Ok(self.ready.lock().unwrap().pop_front().unwrap())
    }
}
fn image() -> Image {
    Image {
        path: "/explicit/good.img".into(),
        sha256: "a".repeat(64),
    }
}

#[test]
fn compiled_fixture_needs_no_recovery_settings_for_power_and_uart() {
    let mut fixture = crate::settings::fixture();
    fixture.recovery.enabled = false;
    fixture.recovery.image = None;
    fixture.recovery.loader.otp_gpio8_provisioned = false;
    fixture.recovery.usb_path.clear();
    fixture.recovery.disk.id_path.clear();
    fixture.recovery.disk.serial.clear();
    fixture.recovery.loader.gadget_sha256.clear();
    fixture.validate().unwrap();
    assert!(fixture.recovery.validate().is_err());
    fixture.recovery.enabled = true;
    assert!(fixture.validate().is_err());
}

#[tokio::test]
async fn disabled_recovery_rejects_flash_and_auto_recovery_before_device_actions() {
    use polis_sbc_controller_host_api::{API_VERSION, Action, Request};
    let temp = Temp::new();
    let (link, transmissions) = crate::link::Link::simulated(&temp.0);
    let mut fixture = crate::settings::fixture();
    fixture.recovery.enabled = false;
    let name = fixture.fixture.clone();
    let runtime = Arc::new(crate::service::Runtime {
        config: Arc::new(fixture),
        link,
        operation: Arc::new(tokio::sync::Mutex::new(())),
        status: Arc::new(tokio::sync::Mutex::new(RunStatus::default())),
    });
    for (image, auto_recover) in [(Some(image()), false), (None, true)] {
        let error = runtime
            .dispatch(Request {
                api_version: API_VERSION,
                fixture: name.clone(),
                action: Action::Workflow {
                    image,
                    auto_recover,
                    confirm_fixture: name.clone(),
                },
            })
            .await
            .unwrap_err();
        assert!(error.to_string().contains("reflashing is disabled"));
    }
    let status = runtime
        .dispatch(Request {
            api_version: API_VERSION,
            fixture: name,
            action: Action::Status,
        })
        .await
        .unwrap();
    assert_eq!(status["recovery_enabled"], false);
    assert_eq!(transmissions.load(std::sync::atomic::Ordering::Relaxed), 0);
    assert!(!runtime.status.lock().await.active);
}
fn journal() -> Journal {
    Journal {
        state: Arc::new(tokio::sync::Mutex::new(RunStatus::default())),
        path: None,
    }
}

#[tokio::test]
async fn initial_flash_verified_before_normal_boot() {
    let sim = Sim::new(&[true], None);
    let j = journal();
    execute(&sim, Some(&image()), None, false, &j)
        .await
        .unwrap();
    assert_eq!(
        *sim.steps.lock().unwrap(),
        [
            "preflight",
            "recovery_boot",
            "gadget",
            "flash_verify",
            "normal_boot",
            "ready"
        ]
    );
    assert_eq!(j.state.lock().await.recovery_attempts, 0);
}
#[tokio::test]
async fn failed_image_recovers_once_and_keeps_original_failure() {
    let sim = Sim::new(&[false, true], None);
    let j = journal();
    execute(&sim, Some(&image()), Some(&image()), true, &j)
        .await
        .unwrap();
    let state = j.state.lock().await;
    assert_eq!(state.recovery_attempts, 1);
    assert_eq!(state.failures.len(), 1);
    assert_eq!(state.stage, "recovered");
    assert_eq!(
        sim.steps
            .lock()
            .unwrap()
            .iter()
            .filter(|s| *s == "flash_verify")
            .count(),
        2
    );
}
#[tokio::test]
async fn failed_recovery_boot_stops_at_one_attempt() {
    let sim = Sim::new(&[false, false], None);
    let j = journal();
    let error = execute(&sim, None, Some(&image()), true, &j)
        .await
        .unwrap_err();
    assert!(error.to_string().contains("exhausted"));
    assert_eq!(j.state.lock().await.recovery_attempts, 1);
    assert_eq!(
        sim.steps
            .lock()
            .unwrap()
            .iter()
            .filter(|s| *s == "flash_verify")
            .count(),
        1
    );
}
#[tokio::test]
async fn recovery_requires_explicit_image_before_any_operation() {
    let sim = Sim::new(&[], None);
    assert!(execute(&sim, None, None, true, &journal()).await.is_err());
    assert!(sim.steps.lock().unwrap().is_empty());
}
#[tokio::test]
async fn ambiguous_power_flash_loader_and_capture_failures_never_retry() {
    for phase in [
        "preflight",
        "recovery_boot",
        "gadget",
        "flash_verify",
        "normal_boot",
        "ready",
    ] {
        let sim = Sim::new(&[], Some(phase));
        let j = journal();
        assert!(
            execute(&sim, Some(&image()), Some(&image()), true, &j)
                .await
                .is_err()
        );
        assert_eq!(j.state.lock().await.recovery_attempts, 0);
        assert_eq!(sim.steps.lock().unwrap().last().unwrap(), phase);
    }
}
#[tokio::test]
async fn disabled_recovery_never_flashes_on_boot_failure() {
    let sim = Sim::new(&[false], None);
    assert!(
        execute(&sim, None, Some(&image()), false, &journal())
            .await
            .is_err()
    );
    assert!(
        !sim.steps
            .lock()
            .unwrap()
            .iter()
            .any(|s| s == "flash_verify")
    );
}
#[test]
fn lost_final_uart_chunk_or_hardware_error_prevents_automatic_reflash() {
    use crate::workflow::validate_capture;
    use polis_sbc_controller_embedded_api::Counters;
    let before = Counters::default();
    for after in [
        Counters {
            uart_rx_lost_bytes: 5,
            ..before
        },
        Counters {
            uart_errors: 1,
            ..before
        },
        Counters {
            uart_overruns: 1,
            ..before
        },
        Counters {
            usb_tx_failures: 1,
            ..before
        },
    ] {
        assert!(validate_capture(before, after).is_err());
    }
    // Deliberate off-state discards do not make a normal boot ambiguous.
    assert!(
        validate_capture(
            before,
            Counters {
                uart_rx_dropped_bytes: 5,
                ..before
            }
        )
        .is_ok()
    );
}

struct Temp(std::path::PathBuf);
impl Temp {
    fn new() -> Self {
        let root = std::env::temp_dir().join(format!(
            "polis-sbc-controller-test-{:016x}",
            rand::random::<u64>()
        ));
        std::fs::create_dir(&root).unwrap();
        Self(root)
    }
}
impl Drop for Temp {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

#[tokio::test]
async fn api_rejects_all_conflicting_mutations_while_status_remains_available() {
    use polis_sbc_controller_host_api::{API_VERSION, Action, Request};
    let temp = Temp::new();
    let (link, transmissions) = crate::link::Link::simulated(&temp.0);
    let mut config = crate::settings::fixture();
    config.fixture = "bench".into();
    config.recovery.image = None;
    let runtime = Arc::new(crate::service::Runtime {
        config: Arc::new(config),
        link,
        operation: Arc::new(tokio::sync::Mutex::new(())),
        status: Arc::new(tokio::sync::Mutex::new(RunStatus::default())),
    });
    let held = runtime.operation.clone().try_lock_owned().unwrap();
    for action in [
        Action::Diagnostics,
        Action::Benchmark {
            samples: 3,
            confirm_fixture: "bench".into(),
        },
        Action::Power {
            on: false,
            confirm_fixture: "bench".into(),
        },
        Action::Cycle {
            off_ms: 1000,
            confirm_fixture: "bench".into(),
        },
        Action::RecoveryUsb {
            confirm_fixture: "bench".into(),
        },
        Action::ConsoleWrite {
            text: "dangerous\n".into(),
        },
        Action::Workflow {
            image: None,
            auto_recover: false,
            confirm_fixture: "bench".into(),
        },
    ] {
        let error = runtime
            .dispatch(Request {
                api_version: API_VERSION,
                fixture: "bench".into(),
                action,
            })
            .await
            .unwrap_err();
        assert!(error.to_string().contains("busy"));
    }
    assert!(
        runtime
            .dispatch(Request {
                api_version: API_VERSION,
                fixture: "bench".into(),
                action: Action::Status
            })
            .await
            .is_ok()
    );
    assert_eq!(transmissions.load(std::sync::atomic::Ordering::Relaxed), 0);
    drop(held);
    assert!(
        runtime
            .dispatch(Request {
                api_version: API_VERSION,
                fixture: "other".into(),
                action: Action::Power {
                    on: false,
                    confirm_fixture: "other".into()
                }
            })
            .await
            .is_err()
    );
    assert_eq!(transmissions.load(std::sync::atomic::Ordering::Relaxed), 0);
}

#[tokio::test]
async fn stale_recovery_uart_cannot_satisfy_new_boot_readiness() {
    let temp = Temp::new();
    let (link, _) = crate::link::Link::simulated(&temp.0);
    let (cursor, gaps) = link.checkpoint(100);
    // Arrives late at the host, but Pico captured it before the normal boot.
    link.inject_uart(99, 0, b"READY");
    let (next, bytes, lost) = link.read(cursor, 4096, 100);
    assert!(bytes.is_empty());
    assert!(!lost);
    link.inject_uart(101, 100, b"REA");
    link.inject_uart(102, 103, b"DY");
    let (_, bytes, lost) = link.read(next, 4096, 100);
    assert_eq!(bytes, b"READY");
    assert!(!lost);
    assert_eq!(link.gaps(), gaps);
    link.inject_uart(103, 999, b"gap");
    assert!(link.gaps() > gaps);
}

#[tokio::test]
async fn bounded_capture_reports_overwritten_cursors() {
    let temp = Temp::new();
    let (link, _) = crate::link::Link::simulated(&temp.0);
    for offset in (0..70000).step_by(128) {
        link.inject_uart(1, offset, &[b'\n'; 128]);
    }
    assert!(link.read(0, 4096, 0).2);
}

#[test]
fn commissioning_requires_otp_and_loader_but_not_disk_or_flash_enable() {
    let mut config = crate::settings::fixture();
    config.recovery.enabled = false;
    config.recovery.disk.id_path.clear();
    config.recovery.disk.serial.clear();
    config.recovery.loader.otp_gpio8_provisioned = true;
    config.validate().unwrap();
    config.recovery.validate_loader().unwrap();
    assert!(config.recovery.validate().is_err());
    config.recovery.loader.otp_gpio8_provisioned = false;
    assert!(config.recovery.validate_loader().is_err());
    config.recovery.loader.otp_gpio8_provisioned = true;
    config.recovery.loader.gadget_sha256.clear();
    assert!(config.recovery.validate_loader().is_err());
}
