//! Standalone boot probe; the Slate BSP remains a separate application dependency.
mod diagnostics;

use std::{
    error::Error,
    fs::{self, OpenOptions},
    io,
    os::unix::{fs::FileTypeExt, fs::OpenOptionsExt},
    path::Path,
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc,
    },
    time::{Duration, Instant as WallInstant},
};

use rppal::gpio::{Gpio, OutputPin};
use tokio::{
    signal::unix::{signal, SignalKind},
    time::{sleep, Instant},
};

const UART_TIMEOUT: Duration = Duration::from_secs(5);
const RETRY_DELAY: Duration = Duration::from_millis(10);
const HALF_PERIOD: Duration = Duration::from_millis(500);

async fn wait_for_export(exported: &AtomicBool) {
    while !exported.load(Ordering::Acquire) {
        sleep(Duration::from_millis(50)).await;
    }
}

async fn export_indicator(pin: &mut OutputPin, exported: &AtomicBool) {
    wait_for_export(exported).await;
    pin.set_high();
    println!(
        "POLIS_GPIO_LOGS_SAVED gpio={} uptime={}",
        pin.pin(),
        uptime()
    );
    blink(|| pin.toggle()).await;
}

// Opening a UART with O_NONBLOCK avoids waiting for carrier detect; O_NOCTTY
// prevents it becoming our controlling terminal. No termios changes or I/O.
fn probe_uart(path: &Path) -> io::Result<()> {
    let port = OpenOptions::new()
        .read(true)
        .write(true)
        .custom_flags(libc::O_NOCTTY | libc::O_NONBLOCK)
        .open(path)?;
    if !port.metadata()?.file_type().is_char_device() {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "UART must be a character device",
        ));
    }
    Ok(()) // Close immediately, leaving the UART available to the application.
}

async fn wait_for_uart(mut probe: impl FnMut() -> io::Result<()>) -> io::Result<()> {
    let deadline = Instant::now() + UART_TIMEOUT;
    loop {
        match probe() {
            Ok(()) => return Ok(()),
            Err(error) => {
                let transient = error.kind() == io::ErrorKind::NotFound
                    || matches!(error.raw_os_error(), Some(libc::ENXIO | libc::ENODEV));
                if !transient {
                    return Err(error);
                }
                if Instant::now() >= deadline {
                    return Err(io::Error::new(
                        io::ErrorKind::TimedOut,
                        format!("UART unavailable after 5 seconds: {error}"),
                    ));
                }
                sleep(RETRY_DELAY).await;
            }
        }
    }
}

async fn blink(mut toggle: impl FnMut()) {
    loop {
        sleep(HALF_PERIOD).await;
        toggle();
    }
    // Cancellation stops toggling; main owns and restores both pins on exit.
}

fn uptime() -> String {
    // A logging failure must not prevent either hardware marker.
    fs::read_to_string("/proc/uptime")
        .ok()
        .and_then(|value| value.split_whitespace().next().map(str::to_owned))
        .unwrap_or_else(|| "unknown".to_owned())
}

async fn uart_indicator(
    pin: &mut OutputPin,
    uart: &Path,
    started: WallInstant,
    startup_log: &str,
    exported: Arc<AtomicBool>,
) -> io::Result<()> {
    let waiting = WallInstant::now();
    wait_for_uart(|| probe_uart(uart))
        .await
        .map_err(|error| io::Error::new(error.kind(), format!("{}: {error}", uart.display())))?;
    let wait_ms = waiting.elapsed().as_secs_f64() * 1000.0;
    pin.set_high();
    let startup_to_uart_ms = started.elapsed().as_secs_f64() * 1000.0;
    let ready_log = format!(
        "POLIS_GPIO_READY gpio={} uart={} uptime={} uart_wait_ms={wait_ms:.3} startup_to_uart_ms={startup_to_uart_ms:.3}",
        pin.pin(),
        uart.display(),
        uptime()
    );
    println!("{ready_log}");
    diagnostics::start(format!("{startup_log}\n{ready_log}\n"), exported);
    blink(|| pin.toggle()).await;
    Ok(())
}

fn parse_pins(args: &[String]) -> Result<(u8, u8), Box<dyn Error>> {
    if args.len() > 3 {
        return Err("expected at most UART_GPIO, UART_PATH, and EXPORT_GPIO arguments".into());
    }
    let ready = args
        .first()
        .map(|arg| arg.parse::<u8>())
        .transpose()?
        .unwrap_or(26);
    let startup = args
        .get(2)
        .map(|arg| arg.parse::<u8>())
        .transpose()?
        .unwrap_or(19);
    for pin in [ready, startup] {
        if pin > 27 || matches!(pin, 14 | 15) {
            return Err("use BCM GPIO 0..=27, excluding reserved UART pins 14 and 15".into());
        }
    }
    if ready == startup {
        return Err("UART and export indicators must use different GPIO pins".into());
    }
    Ok((ready, startup))
}

fn main() -> Result<(), Box<dyn Error>> {
    let args: Vec<String> = std::env::args().skip(1).collect();
    if args.first().is_some_and(|arg| arg == "--help") {
        println!("Usage: polis-gpio-test [UART_GPIO [UART_PATH [EXPORT_GPIO]]]\nDefaults: 26 /dev/ttyAMA0 19\nUART GPIO goes high when the device opens. Export GPIO goes high after boot logs are saved and flushed to /data.\nEach toggles every 500 ms after its own milestone. UART errors are also exported.\nWait up to 5 seconds for UART. No UART data is sent.");
        return Ok(());
    }
    let (ready_bcm, export_bcm) = parse_pins(&args)?;
    let uart = Path::new(args.get(1).map(String::as_str).unwrap_or("/dev/ttyAMA0"));

    // Record application startup in software; both LEDs wait for their milestone.
    let gpio = Gpio::new()?;
    let mut export_pin = gpio.get(export_bcm)?.into_output_low();
    let started = WallInstant::now();
    let mut ready_pin = gpio.get(ready_bcm)?.into_output_low();
    let startup_log = format!(
        "POLIS_GPIO_START uart_gpio={ready_bcm} export_gpio={export_bcm} uptime={}",
        uptime()
    );
    println!("{startup_log}");
    let exported = Arc::new(AtomicBool::new(false));

    tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()?
        .block_on(async {
            let mut terminate = signal(SignalKind::terminate())?;
            let mut interrupt = signal(SignalKind::interrupt())?;
            tokio::select! {
                _ = export_indicator(&mut export_pin, &exported) => {},
                _ = async {
                    if let Err(error) = uart_indicator(&mut ready_pin, uart, started, &startup_log, Arc::clone(&exported)).await {
                        let error_log = format!("POLIS_UART_ERROR uart={} uptime={} error={error}", uart.display(), uptime());
                        eprintln!("{error_log}");
                        diagnostics::start(format!("{startup_log}\n{error_log}\n"), Arc::clone(&exported));
                    }
                    // Keep waiting for the export indicator even if UART fails.
                    std::future::pending::<()>().await;
                } => {},
                _ = terminate.recv() => {},
                _ = interrupt.recv() => {},
            }
            Ok::<(), io::Error>(())
        })?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn distinct_pins_preserve_existing_uart_arguments() {
        assert_eq!(parse_pins(&[]).unwrap(), (26, 19));
        let args = ["21", "/dev/ttyAMA1", "20"].map(str::to_owned);
        assert_eq!(parse_pins(&args).unwrap(), (21, 20));
        assert_eq!(parse_pins(&args[..2]).unwrap(), (21, 19));
        for (ready, startup) in [(19, 19), (14, 19), (26, 15), (28, 19), (26, 28)] {
            let args = [
                ready.to_string(),
                "/dev/ttyAMA0".into(),
                startup.to_string(),
            ];
            assert!(parse_pins(&args).is_err());
        }
    }

    #[tokio::test(start_paused = true)]
    async fn available_uart_has_no_retry_delay() {
        let start = Instant::now();
        wait_for_uart(|| Ok(())).await.unwrap();
        assert_eq!(Instant::now(), start);
    }

    #[tokio::test(start_paused = true)]
    async fn export_indicator_waits_until_storage_worker_reports_success() {
        let exported = AtomicBool::new(false);
        tokio::select! {
            _ = wait_for_export(&exported) => panic!("export LED became ready before files were flushed"),
            _ = sleep(Duration::from_secs(2)) => {},
        }
        exported.store(true, Ordering::Release);
        let start = Instant::now();
        wait_for_export(&exported).await;
        assert_eq!(Instant::now(), start);
    }

    #[tokio::test(start_paused = true)]
    async fn waits_for_uart_before_returning_ready() {
        let start = Instant::now();
        let mut attempts = 0;
        wait_for_uart(|| {
            attempts += 1;
            if attempts < 3 {
                Err(io::ErrorKind::NotFound.into())
            } else {
                Ok(())
            }
        })
        .await
        .unwrap();
        assert_eq!(attempts, 3);
        assert_eq!(Instant::now() - start, RETRY_DELAY * 2);
    }

    #[tokio::test(start_paused = true)]
    async fn absent_uart_has_a_bounded_timeout() {
        let start = Instant::now();
        let error = wait_for_uart(|| Err(io::Error::from_raw_os_error(libc::ENXIO)))
            .await
            .unwrap_err();
        assert_eq!(error.kind(), io::ErrorKind::TimedOut);
        assert_eq!(Instant::now() - start, UART_TIMEOUT);
    }

    #[tokio::test(start_paused = true)]
    async fn permissions_fail_immediately() {
        let start = Instant::now();
        let error = wait_for_uart(|| Err(io::ErrorKind::PermissionDenied.into()))
            .await
            .unwrap_err();
        assert_eq!(error.kind(), io::ErrorKind::PermissionDenied);
        assert_eq!(Instant::now(), start);
    }

    #[test]
    fn probe_accepts_character_devices_without_transmitting() {
        probe_uart(Path::new("/dev/null")).unwrap();
        // Cargo.toml is writable in the test tree, but must not count as a UART.
        assert_eq!(
            probe_uart(Path::new("Cargo.toml")).unwrap_err().kind(),
            io::ErrorKind::InvalidInput
        );
    }
}
