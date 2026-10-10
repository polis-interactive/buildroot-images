use anyhow::{Context, Result, bail, ensure};
use polis_wired_framing::Decoder;
use polis_wired_host_api::Config;
use polis_wired_protocol::{
    Command, Key, MAX_PAYLOAD, Message, Reply, Request, Status, WIRE_CAPACITY,
};
use serde::Serialize;
use std::{
    collections::VecDeque,
    fs::{File, OpenOptions},
    io::Write,
    sync::{Arc, Mutex},
};
use tokio::{
    io::{AsyncRead, AsyncReadExt, AsyncWrite, AsyncWriteExt},
    sync::{mpsc, oneshot, watch},
    time::{Duration, Instant, timeout},
};
use tokio_serial::{SerialPortBuilderExt, SerialStream};

#[derive(Clone, Debug, Default, Serialize)]
pub struct Connection {
    pub connected: bool,
    pub generation: u64,
    pub boot_id: u64,
    pub status: Option<Status>,
    pub error: Option<String>,
}
struct Call {
    generation: u64,
    command: Command,
    reply: oneshot::Sender<Result<Reply>>,
}
struct Chunk {
    start: u64,
    timestamp_us: u64,
    bytes: Vec<u8>,
}
struct Capture {
    console: crate::logging::UartConsole,
    chunks: VecDeque<Chunk>,
    end: u64,
    size: usize,
    expected_pico_offset: Option<u64>,
    gaps: u64,
    readiness_since_us: u64,
    raw: File,
    events: File,
}
impl Capture {
    fn receive(&mut self, value: polis_wired_protocol::UartRx) -> Result<()> {
        if value.timestamp_us >= self.readiness_since_us {
            if self
                .expected_pico_offset
                .is_some_and(|offset| offset != value.offset)
            {
                self.gaps += 1;
            }
            self.expected_pico_offset = Some(value.offset + value.bytes.len() as u64);
        }
        serde_json::to_writer(
            &mut self.events,
            &serde_json::json!({"host_offset":self.end,"uart":value}),
        )?;
        self.events.write_all(b"\n")?;
        self.raw.write_all(&value.bytes)?;
        self.console.feed(
            value.session,
            value.offset,
            value.timestamp_us,
            &value.bytes,
            crate::logging::event,
        );
        self.chunks.push_back(Chunk {
            start: self.end,
            timestamp_us: value.timestamp_us,
            bytes: value.bytes.to_vec(),
        });
        self.end += value.bytes.len() as u64;
        self.size += value.bytes.len();
        while self.size > 65536 {
            self.size -= self.chunks.pop_front().unwrap().bytes.len();
        }
        Ok(())
    }
}

#[derive(Clone)]
pub struct Link {
    calls: mpsc::Sender<Call>,
    pub state: watch::Receiver<Connection>,
    capture: Arc<Mutex<Capture>>,
}
impl Link {
    pub fn start(config: Arc<Config>) -> Result<Self> {
        let raw = OpenOptions::new()
            .create(true)
            .append(true)
            .open(config.state_dir.join("uart.bin"))?;
        let end = raw.metadata()?.len();
        let events = OpenOptions::new()
            .create(true)
            .append(true)
            .open(config.state_dir.join("uart.jsonl"))?;
        let capture = Arc::new(Mutex::new(Capture {
            console: Default::default(),
            chunks: VecDeque::new(),
            end,
            size: 0,
            expected_pico_offset: None,
            gaps: 0,
            readiness_since_us: 0,
            raw,
            events,
        }));
        let (calls, rx) = mpsc::channel(8);
        let console_capture = Arc::downgrade(&capture);
        tokio::spawn(async move {
            let mut tick = tokio::time::interval(Duration::from_millis(250));
            loop {
                tick.tick().await;
                let Some(capture) = console_capture.upgrade() else {
                    break;
                };
                capture
                    .lock()
                    .unwrap()
                    .console
                    .flush_idle(std::time::Instant::now(), crate::logging::event);
            }
        });
        let (tx, state) = watch::channel(Connection::default());
        tokio::spawn(worker(config, rx, tx, capture.clone()));
        Ok(Self {
            calls,
            state,
            capture,
        })
    }
    pub fn generation(&self) -> Result<u64> {
        let state = self.state.borrow();
        ensure!(
            state.connected,
            "Pico disconnected: {}",
            state.error.as_deref().unwrap_or("connecting")
        );
        Ok(state.generation)
    }
    pub fn require_generation(&self, expected: u64) -> Result<()> {
        ensure!(
            self.generation()? == expected,
            "Pico connection changed; operation outcome uncertain, no replay"
        );
        Ok(())
    }
    pub async fn command(&self, command: Command) -> Result<Reply> {
        let generation = self.generation()?;
        let (reply, response) = oneshot::channel();
        self.calls
            .send(Call {
                generation,
                command,
                reply,
            })
            .await?;
        response
            .await
            .context("Pico worker ended; operation outcome uncertain")?
    }
    pub fn cursor(&self) -> u64 {
        self.capture.lock().unwrap().end
    }
    pub fn checkpoint(&self, pico_us: u64) -> (u64, u64) {
        let mut capture = self.capture.lock().unwrap();
        capture.readiness_since_us = pico_us;
        capture.expected_pico_offset = None;
        (capture.end, capture.gaps)
    }
    pub fn gaps(&self) -> u64 {
        self.capture.lock().unwrap().gaps
    }
    pub fn sync_logs(&self) -> Result<()> {
        let cap = self.capture.lock().unwrap();
        cap.raw.sync_all()?;
        cap.events.sync_all()?;
        Ok(())
    }
    pub fn read(&self, after: u64, max: usize, since_us: u64) -> (u64, Vec<u8>, bool) {
        let cap = self.capture.lock().unwrap();
        let oldest = cap.chunks.front().map_or(cap.end, |c| c.start);
        let mut next = after.max(oldest).min(cap.end);
        let mut bytes = Vec::new();
        for chunk in &cap.chunks {
            let end = chunk.start + chunk.bytes.len() as u64;
            if end <= next {
                continue;
            }
            if chunk.timestamp_us < since_us {
                next = end;
                continue;
            }
            let start = next.saturating_sub(chunk.start) as usize;
            let n = (chunk.bytes.len() - start).min(max.saturating_sub(bytes.len()));
            bytes.extend_from_slice(&chunk.bytes[start..start + n]);
            next = chunk.start + (start + n) as u64;
            if bytes.len() == max {
                break;
            }
        }
        (next, bytes, after < oldest)
    }
}

struct Wire {
    port: SerialStream,
    storage: [u8; WIRE_CAPACITY],
    pending: VecDeque<Message>,
}
// Decoder borrows its buffer; keep it scoped to a connection rather than
// reconstructing it per read (partial USB frames must survive short reads).
async fn receive(
    port: &mut (impl AsyncRead + Unpin),
    decoder: &mut Decoder<'_>,
    queue: &mut VecDeque<Message>,
) -> Result<Message> {
    loop {
        if let Some(message) = queue.pop_front() {
            return Ok(message);
        }
        let mut data = [0; 512];
        let len = port.read(&mut data).await?;
        ensure!(len != 0, "Pico USB EOF");
        let mut error = None;
        decoder.feed(&data[..len], |frame| match frame {
            Ok(frame) => match Message::decode(frame) {
                Ok(message) => queue.push_back(message),
                Err(e) => error = Some(format!("Pico protocol/schema mismatch: {e:?}")),
            },
            Err(e) => error = Some(format!("Pico framing error: {e:?}")),
        });
        if let Some(error) = error {
            bail!(error);
        }
    }
}
async fn send(port: &mut (impl AsyncWrite + Unpin), request: Request) -> Result<()> {
    let mut scratch = [0; MAX_PAYLOAD as usize];
    let mut wire = [0; WIRE_CAPACITY];
    let message = Message::Request(request);
    let bytes = message
        .encode(&mut scratch, &mut wire)
        .map_err(|e| anyhow::anyhow!("encode: {e:?}"))?;
    timeout(
        Duration::from_secs(2),
        AsyncWriteExt::write_all(port, bytes),
    )
    .await
    .context("USB write timeout; outcome uncertain")??;
    Ok(())
}
/// Keep sending read-only presence probes while a long power cycle is pending.
/// New IDs are allocated for probes; the original operation is never resent.
async fn exchange(
    port: &mut (impl AsyncRead + AsyncWrite + Unpin),
    decoder: &mut Decoder<'_>,
    pending: &mut VecDeque<Message>,
    session: u64,
    id: &mut u32,
    command: Command,
    mut observe: impl FnMut(&Message) -> Result<()>,
) -> Result<Reply> {
    let seconds = match &command {
        Command::CyclePower { off_ms } | Command::Boot { off_ms, .. } => {
            u64::from(*off_ms) / 1000 + 5
        }
        _ => 5,
    };
    let probe = command == Command::Status;
    *id = id
        .checked_add(1)
        .context("request IDs exhausted; reconnect required")?;
    let key = Key { session, id: *id };
    send(port, Request { key, command }).await?;
    let deadline = Instant::now() + Duration::from_secs(seconds);
    let mut next_ping = Instant::now() + Duration::from_secs(1);
    let mut ping: Option<(Key, Instant)> = None;
    loop {
        tokio::select! {
            incoming = tokio::time::timeout_at(deadline, receive(port, decoder, pending)) => {
                let message = incoming.context("Pico reply timeout; outcome uncertain, no replay")??;
                observe(&message)?;
                if let Message::Response(response) = message {
                    if response.key == key && response.reply != Reply::Accepted {
                        return Ok(response.reply);
                    }
                    if ping.is_some_and(|(key, _)| key == response.key) {
                        ensure!(matches!(response.reply, Reply::Status(_)), "Pico rejected presence probe");
                        ping = None;
                    }
                }
            }
            _ = tokio::time::sleep_until(next_ping), if !probe => {
                if let Some((_, deadline)) = ping {
                    ensure!(Instant::now() < deadline, "Pico presence probe timed out; no replay");
                } else {
                    *id = id.checked_add(1).context("request IDs exhausted; reconnect required")?;
                    let ping_key = Key { session, id: *id };
                    send(port, Request { key: ping_key, command: Command::Status }).await?;
                    ping = Some((ping_key, Instant::now() + Duration::from_secs(3)));
                }
                next_ping = Instant::now() + Duration::from_secs(1);
            }
        }
    }
}
fn observe(
    message: &Message,
    session: u64,
    capture: &Arc<Mutex<Capture>>,
    state: &watch::Sender<Connection>,
) -> Result<()> {
    match message {
        Message::UartRx(value) if value.session == session => {
            capture.lock().unwrap().receive(value.clone())?
        }
        Message::Heartbeat(value) if value.session == session => {
            state.send_modify(|s| s.status = Some(value.status))
        }
        Message::Response(value) if value.key.session == session => {
            if let Reply::Status(status) = value.reply {
                state.send_modify(|s| s.status = Some(status));
            }
        }
        _ => {}
    }
    Ok(())
}
async fn connected(
    config: &Config,
    calls: &mut mpsc::Receiver<Call>,
    state: &watch::Sender<Connection>,
    capture: &Arc<Mutex<Capture>>,
) -> Result<()> {
    let mut port =
        tokio_serial::new(config.pico_path.to_string_lossy(), 115200).open_native_async()?;
    port.set_exclusive(true)?;
    let mut wire = Wire {
        port,
        storage: [0; WIRE_CAPACITY],
        pending: VecDeque::new(),
    };
    let mut decoder = Decoder::new(&mut wire.storage);
    let session = rand::random::<u64>().max(1);
    let mut id = 1u32;
    let hello_key = Key { session, id };
    send(
        &mut wire.port,
        Request {
            key: hello_key,
            command: Command::Hello,
        },
    )
    .await?;
    let hello = timeout(Duration::from_secs(3), async {
        loop {
            if let Message::Response(response) =
                receive(&mut wire.port, &mut decoder, &mut wire.pending).await?
                && response.key == hello_key
            {
                return Ok::<_, anyhow::Error>(response.reply);
            }
        }
    })
    .await??;
    let Reply::Hello {
        device_id,
        boot_id,
        status,
        ..
    } = hello
    else {
        bail!("invalid Pico Hello response");
    };
    let identity: String = device_id.iter().map(|b| format!("{b:02x}")).collect();
    ensure!(
        identity.eq_ignore_ascii_case(&config.pico_id),
        "Pico chip ID mismatch"
    );
    capture.lock().unwrap().expected_pico_offset = Some(status.uart_rx_offset);
    state.send_modify(|s| {
        s.generation += 1;
        s.connected = true;
        s.boot_id = boot_id;
        s.status = Some(status);
        s.error = None;
    });
    let generation = state.borrow().generation;
    let mut next_probe = Instant::now() + Duration::from_secs(1);
    loop {
        tokio::select! {
            incoming = timeout(Duration::from_secs(4), receive(&mut wire.port, &mut decoder, &mut wire.pending)) => {
                let message = incoming.context("Pico heartbeat timeout")??;
                observe(&message, session, capture, state)?;
            }
            call = calls.recv() => {
                let Some(call) = call else { bail!("daemon stopped"); };
                if call.generation != generation { let _ = call.reply.send(Err(anyhow::anyhow!("connection changed; command not sent"))); continue; }
                let result = exchange(
                    &mut wire.port, &mut decoder, &mut wire.pending, session, &mut id,
                    call.command, |message| observe(message, session, capture, state),
                ).await;
                next_probe = Instant::now() + Duration::from_secs(1);
                let failed = result.is_err();
                let _ = call.reply.send(result);
                if failed { bail!("command transport failed; outcome uncertain, no replay"); }
            }
            _ = tokio::time::sleep_until(next_probe) => {
                let reply = exchange(
                    &mut wire.port, &mut decoder, &mut wire.pending, session, &mut id,
                    Command::Status, |message| observe(message, session, capture, state),
                ).await?;
                ensure!(matches!(reply, Reply::Status(_)), "Pico rejected presence probe");
                next_probe = Instant::now() + Duration::from_secs(1);
            }
        }
    }
}
async fn worker(
    config: Arc<Config>,
    mut calls: mpsc::Receiver<Call>,
    state: watch::Sender<Connection>,
    capture: Arc<Mutex<Capture>>,
) {
    loop {
        if let Err(error) = connected(&config, &mut calls, &state, &capture).await {
            capture
                .lock()
                .unwrap()
                .console
                .flush("disconnected", crate::logging::event);
            state.send_modify(|s| {
                s.connected = false;
                s.error = Some(format!("{error:#}"));
            });
        }
        while let Ok(call) = calls.try_recv() {
            let _ = call.reply.send(Err(anyhow::anyhow!(
                "Pico disconnected; command not replayed"
            )));
        }
        tokio::time::sleep(Duration::from_secs(1)).await;
    }
}

#[cfg(test)]
impl Link {
    pub fn simulated(root: &std::path::Path) -> (Self, Arc<std::sync::atomic::AtomicUsize>) {
        let raw = File::create(root.join("uart.bin")).unwrap();
        let events = File::create(root.join("uart.jsonl")).unwrap();
        let capture = Arc::new(Mutex::new(Capture {
            console: Default::default(),
            chunks: VecDeque::new(),
            end: 0,
            size: 0,
            expected_pico_offset: None,
            gaps: 0,
            readiness_since_us: 0,
            raw,
            events,
        }));
        let (calls, mut rx) = mpsc::channel::<Call>(8);
        let (_, state) = watch::channel(Connection {
            connected: true,
            generation: 1,
            ..Default::default()
        });
        let transmissions = Arc::new(std::sync::atomic::AtomicUsize::new(0));
        let count = transmissions.clone();
        tokio::spawn(async move {
            while let Some(call) = rx.recv().await {
                count.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
                let _ = call.reply.send(Err(anyhow::anyhow!(
                    "simulated transport failure; no replay"
                )));
            }
        });
        (
            Self {
                calls,
                state,
                capture,
            },
            transmissions,
        )
    }
    pub fn inject_uart(&self, timestamp_us: u64, offset: u64, bytes: &[u8]) {
        self.capture
            .lock()
            .unwrap()
            .receive(polis_wired_protocol::UartRx {
                session: 1,
                timestamp_us,
                offset,
                bytes: polis_wired_protocol::Bytes::from_slice(bytes).unwrap(),
            })
            .unwrap();
    }
}

#[cfg(test)]
mod keepalive_tests {
    use super::*;
    use polis_wired_protocol::{BootMode, Counters, ErrorCode, Response};

    fn status() -> Status {
        Status {
            timing: Default::default(),
            boot_mode: BootMode::Normal,
            uptime_us: 0,
            power_on: false,
            cycle: None,
            uart_baud: 115200,
            uart_tx_busy: false,
            uart_rx_offset: 0,
            counters: Counters::default(),
        }
    }

    async fn reply(port: &mut tokio::io::DuplexStream, key: Key, reply: Reply) {
        let mut scratch = [0; MAX_PAYLOAD as usize];
        let mut wire = [0; WIRE_CAPACITY];
        let message = Message::Response(Response { key, reply });
        port.write_all(message.encode(&mut scratch, &mut wire).unwrap())
            .await
            .unwrap();
    }

    #[tokio::test(start_paused = true)]
    async fn long_cycle_keeps_presence_alive_without_repeating_power_operation() {
        let (mut host, mut pico) = tokio::io::duplex(4096);
        let server = tokio::spawn(async move {
            let mut storage = [0; WIRE_CAPACITY];
            let mut decoder = Decoder::new(&mut storage);
            let mut pending = VecDeque::new();
            let Message::Request(first) = receive(&mut pico, &mut decoder, &mut pending)
                .await
                .unwrap()
            else {
                panic!()
            };
            assert_eq!(first.command, Command::CyclePower { off_ms: 4000 });
            reply(&mut pico, first.key, Reply::Accepted).await;
            for expected_id in 3..=5 {
                let Message::Request(probe) = receive(&mut pico, &mut decoder, &mut pending)
                    .await
                    .unwrap()
                else {
                    panic!()
                };
                assert_eq!(probe.command, Command::Status);
                assert_eq!(
                    probe.key,
                    Key {
                        session: 7,
                        id: expected_id
                    }
                );
                reply(&mut pico, probe.key, Reply::Status(status())).await;
            }
            reply(&mut pico, first.key, Reply::Completed).await;
        });
        let mut storage = [0; WIRE_CAPACITY];
        let mut decoder = Decoder::new(&mut storage);
        let mut id = 1;
        let mut observations = 0;
        let result = exchange(
            &mut host,
            &mut decoder,
            &mut VecDeque::new(),
            7,
            &mut id,
            Command::CyclePower { off_ms: 4000 },
            |_| {
                observations += 1;
                Ok(())
            },
        )
        .await
        .unwrap();
        assert_eq!(result, Reply::Completed);
        assert_eq!(id, 5);
        assert_eq!(observations, 5); // Accepted, three probes, Completed.
        server.await.unwrap();
    }

    #[tokio::test(start_paused = true)]
    async fn rejected_probe_stops_pending_operation_without_replay() {
        let (mut host, mut pico) = tokio::io::duplex(4096);
        let server = tokio::spawn(async move {
            let mut storage = [0; WIRE_CAPACITY];
            let mut decoder = Decoder::new(&mut storage);
            let mut pending = VecDeque::new();
            let Message::Request(first) = receive(&mut pico, &mut decoder, &mut pending)
                .await
                .unwrap()
            else {
                panic!()
            };
            reply(&mut pico, first.key, Reply::Accepted).await;
            let Message::Request(probe) = receive(&mut pico, &mut decoder, &mut pending)
                .await
                .unwrap()
            else {
                panic!()
            };
            assert_eq!(probe.command, Command::Status);
            reply(&mut pico, probe.key, Reply::Error(ErrorCode::NeedHello)).await;
            let mut byte = [0];
            assert_eq!(pico.read(&mut byte).await.unwrap(), 0);
        });
        let mut storage = [0; WIRE_CAPACITY];
        let mut decoder = Decoder::new(&mut storage);
        let mut id = 1;
        let error = exchange(
            &mut host,
            &mut decoder,
            &mut VecDeque::new(),
            7,
            &mut id,
            Command::Boot {
                mode: BootMode::Recovery,
                off_ms: 4000,
            },
            |_| Ok(()),
        )
        .await
        .unwrap_err();
        assert!(error.to_string().contains("presence probe"));
        assert_eq!(id, 3);
        drop(host);
        server.await.unwrap();
    }
}
