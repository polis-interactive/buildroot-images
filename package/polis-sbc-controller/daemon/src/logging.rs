//! Console diagnostics must never hold up Pico traffic on a slow terminal.
use crate::settings;
use polis_sbc_controller_host_api::Action;
use std::{
    io::Write,
    sync::{
        OnceLock,
        atomic::{AtomicU64, Ordering},
        mpsc::{self, SyncSender},
    },
    time::{Duration, Instant},
};

static OUTPUT: OnceLock<SyncSender<String>> = OnceLock::new();
static DROPPED: AtomicU64 = AtomicU64::new(0);
static NEXT_REQUEST: AtomicU64 = AtomicU64::new(1);

pub fn event(message: String) {
    let output = OUTPUT.get_or_init(|| {
        let (tx, rx) = mpsc::sync_channel::<String>(settings::CONSOLE_LOG_QUEUE);
        std::thread::spawn(move || {
            let stderr = std::io::stderr();
            while let Ok(message) = rx.recv() {
                let mut writer = stderr.lock();
                if writeln!(writer, "{message}").is_err() {
                    break;
                }
                let dropped = DROPPED.swap(0, Ordering::Relaxed);
                if dropped != 0 {
                    let _ = writeln!(writer, "[logs] {dropped} console messages omitted: terminal too slow; UART capture files retain the stream");
                }
            }
        });
        tx
    });
    if output.try_send(message).is_err() {
        DROPPED.fetch_add(1, Ordering::Relaxed);
    }
}

/// Escape terminal control bytes and keep client-supplied fields bounded.
pub fn text(bytes: &[u8]) -> String {
    let mut result = String::new();
    for &byte in bytes.iter().take(settings::UART_CONSOLE_LINE_BYTES) {
        match byte {
            b' '..=b'~' => result.push(byte as char),
            _ => {
                use std::fmt::Write;
                let _ = write!(result, "\\x{byte:02x}");
            }
        }
    }
    if bytes.len() > settings::UART_CONSOLE_LINE_BYTES {
        result.push_str("...");
    }
    result
}

pub fn action_summary(action: &Action) -> String {
    match action {
        Action::Status => "status".into(),
        Action::Images => "images".into(),
        Action::Diagnostics => "diagnostics".into(),
        Action::Benchmark { samples, .. } => format!("benchmark samples={samples}"),
        Action::RecoveryUsb { .. } => "recovery_usb (RAM only)".into(),
        Action::ConsoleRead { after, max_bytes } => {
            format!("console_read after={after} max_bytes={max_bytes}")
        }
        Action::ConsoleWrite { text } => format!("console_write bytes={}", text.len()),
        Action::Power { on, .. } => format!("power on={on}"),
        Action::Cycle { off_ms, .. } => format!("cycle off_ms={off_ms}"),
        Action::Workflow {
            image,
            auto_recover,
            ..
        } => format!(
            "workflow flash={} auto_recover={auto_recover}",
            image.is_some()
        ),
    }
}

pub struct RequestLog {
    id: u64,
    started: Instant,
}
impl RequestLog {
    pub fn received(fixture: &str, action: &Action) -> Self {
        let id = NEXT_REQUEST.fetch_add(1, Ordering::Relaxed);
        event(format!(
            "[CLI/MCP #{id}] received fixture={} {}",
            text(fixture.as_bytes()),
            action_summary(action)
        ));
        Self {
            id,
            started: Instant::now(),
        }
    }
    pub fn finished(&self, result: &anyhow::Result<serde_json::Value>) {
        let outcome = match result {
            Ok(value) if value["started"] == true => format!("started run_id={}", value["run_id"]),
            Ok(_) => "completed".into(),
            Err(error) => format!("failed: {}", text(format!("{error:#}").as_bytes())),
        };
        event(format!(
            "[CLI/MCP #{}] {outcome} elapsed_ms={}",
            self.id,
            self.started.elapsed().as_millis()
        ));
    }
}

#[derive(Default)]
pub struct UartConsole {
    bytes: Vec<u8>,
    first: Option<(u64, u64)>,
    expected: Option<(u64, u64)>,
    last: Option<Instant>,
}
impl UartConsole {
    pub fn feed(
        &mut self,
        session: u64,
        offset: u64,
        timestamp: u64,
        bytes: &[u8],
        mut emit: impl FnMut(String),
    ) {
        if self
            .expected
            .is_some_and(|expected| expected != (session, offset))
        {
            self.flush("stream changed", &mut emit);
        }
        self.expected = Some((session, offset + bytes.len() as u64));
        self.last = Some(Instant::now());
        for (index, &byte) in bytes.iter().enumerate() {
            if byte == b'\r' || byte == b'\n' {
                self.flush("", &mut emit);
            } else {
                self.first.get_or_insert((timestamp, offset + index as u64));
                self.bytes.push(byte);
                if self.bytes.len() == settings::UART_CONSOLE_LINE_BYTES {
                    self.flush("continued", &mut emit);
                }
            }
        }
    }
    pub fn flush_idle(&mut self, now: Instant, emit: impl FnMut(String)) {
        if self.last.is_some_and(|last| {
            now.saturating_duration_since(last)
                >= Duration::from_millis(settings::UART_CONSOLE_IDLE_MS)
        }) {
            self.flush("partial", emit);
        }
    }
    pub fn flush(&mut self, reason: &str, mut emit: impl FnMut(String)) {
        if self.bytes.is_empty() {
            return;
        }
        let message = text(&self.bytes);
        let lower = message.to_ascii_lowercase();
        let level = if ["panic", "error", "fail", "under-voltage", "undervoltage"]
            .iter()
            .any(|word| lower.contains(word))
        {
            "WARN"
        } else if message.contains("POLIS_") {
            "MARKER"
        } else {
            "INFO"
        };
        let (timestamp, offset) = self.first.take().unwrap();
        let suffix = if reason.is_empty() {
            String::new()
        } else {
            format!(" [{reason}]")
        };
        emit(format!(
            "[UART {level} pico_us={timestamp} offset={offset}] {message}{suffix}"
        ));
        self.bytes.clear();
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn split_marker_and_crlf_are_one_readable_event() {
        let mut console = UartConsole::default();
        let mut lines = Vec::new();
        console.feed(1, 0, 123, b"POLIS_SMOKE_", |s| lines.push(s));
        assert!(lines.is_empty());
        console.feed(1, 12, 124, b"READY init\r\n", |s| lines.push(s));
        assert_eq!(
            lines,
            ["[UART MARKER pico_us=123 offset=0] POLIS_SMOKE_READY init"]
        );
    }
    #[test]
    fn gaps_never_join_unrelated_uart_and_control_bytes_are_escaped() {
        let mut console = UartConsole::default();
        let mut lines = Vec::new();
        console.feed(1, 0, 1, b"POLIS_", |s| lines.push(s));
        console.feed(1, 100, 2, b"panic\x1b[2J\0\n", |s| lines.push(s));
        assert!(lines[0].ends_with("POLIS_ [stream changed]"));
        assert!(lines[1].contains("UART WARN"));
        assert!(lines[1].contains("panic\\x1b[2J\\x00"));
    }
    #[test]
    fn long_lines_are_bounded_and_unterminated_prompts_flush_on_idle() {
        let mut console = UartConsole::default();
        let mut lines = Vec::new();
        console.feed(
            1,
            0,
            1,
            &vec![b'x'; settings::UART_CONSOLE_LINE_BYTES],
            |s| lines.push(s),
        );
        assert!(lines[0].ends_with("[continued]"));
        console.feed(
            1,
            settings::UART_CONSOLE_LINE_BYTES as u64,
            2,
            b"login: ",
            |s| lines.push(s),
        );
        console.flush_idle(Instant::now() + Duration::from_secs(2), |s| lines.push(s));
        assert!(lines[1].ends_with("login:  [partial]"));
    }
    #[test]
    fn console_write_summary_omits_payload() {
        assert_eq!(
            action_summary(&Action::ConsoleWrite {
                text: "secret\n".into()
            }),
            "console_write bytes=7"
        );
    }
}
