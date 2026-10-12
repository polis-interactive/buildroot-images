use crate::config::Config;
use anyhow::{Context, Result, bail, ensure};
use polis_control_link_tokio::{
    Buffers,
    protocol::{Requests, StateError},
    serial,
    stream::{Receiver, Sender},
};
use polis_sbc_controller_embedded_api::{
    CONTROL_LINK, Command, Key, MAX_BODY, MAX_PAYLOAD, Message, Reply, Request, Status,
    USB_PRODUCT, WIRE_CAPACITY,
};
use serde::Serialize;
use std::{
    collections::VecDeque,
    fs::{File, OpenOptions},
    io::Write,
    num::NonZeroU64,
    os::unix::fs::MetadataExt,
    sync::{Arc, Mutex},
};
use tokio::{
    io::{AsyncRead, AsyncWrite},
    sync::{mpsc, oneshot, watch},
    time::{Duration, Instant, timeout},
};
use tokio_serial::{SerialPortBuilderExt, SerialPortInfo, SerialPortType};

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
    fn receive(&mut self, value: polis_sbc_controller_embedded_api::UartRx) -> Result<()> {
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
            .open(config.paths.state_dir.join("uart.bin"))?;
        let end = raw.metadata()?.len();
        let events = OpenOptions::new()
            .create(true)
            .append(true)
            .open(config.paths.state_dir.join("uart.jsonl"))?;
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
        tokio::spawn(worker(rx, tx, capture.clone()));
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

// One tracker covers the operation and an independent read-only presence probe.
// Decoder storage and unread bytes belong to the shared receiver for its lifetime.
async fn receive<R: AsyncRead + Unpin>(
    receiver: &mut Receiver<'_, R>,
    requests: &mut Requests<2>,
) -> Result<Message> {
    loop {
        let message = receiver
            .receive(|message| -> Result<Option<Message>> {
                match requests.receive(message) {
                    Ok(_) => Ok(Some(Message::from_link(message)?)),
                    // Replaced sessions and late replies to retired probes are harmless.
                    Err(StateError::WrongSession | StateError::UnknownRequest) => Ok(None),
                    Err(error) => Err(error.into()),
                }
            })
            .await??;
        if let Some(message) = message {
            return Ok(message);
        }
    }
}
async fn send<W: AsyncWrite + Unpin>(sender: &mut Sender<'_, W>, request: Request) -> Result<()> {
    let mut body = [0; MAX_BODY];
    let message = Message::Request(request);
    timeout(
        Duration::from_secs(2),
        sender.send(message.to_link(&mut body)?),
    )
    .await
    .context("USB write timeout; outcome uncertain")??;
    Ok(())
}
/// Keep sending read-only presence probes while a long power cycle is pending.
/// New IDs are allocated for probes; the original operation is never resent.
async fn exchange<R: AsyncRead + Unpin, W: AsyncWrite + Unpin>(
    sender: &mut Sender<'_, W>,
    receiver: &mut Receiver<'_, R>,
    requests: &mut Requests<2>,
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
    let key: Key = requests.reserve()?.into();
    send(sender, Request { key, command }).await?;
    let deadline = Instant::now() + Duration::from_secs(seconds);
    let mut next_ping = Instant::now() + Duration::from_secs(1);
    let mut ping: Option<(Key, Instant)> = None;
    loop {
        tokio::select! {
            incoming = tokio::time::timeout_at(deadline, receive(receiver, requests)) => {
                let message = incoming.context("Pico reply timeout; outcome uncertain, no replay")??;
                observe(&message)?;
                if let Message::Response(response) = message {
                    if response.key == key && response.reply != Reply::Accepted {
                        if let Some((key, _)) = ping { requests.cancel(key.try_into()?)?; }
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
                    let ping_key = requests.reserve()?.into();
                    send(sender, Request { key: ping_key, command: Command::Status }).await?;
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
fn select_port(ports: Vec<SerialPortInfo>) -> Result<String> {
    ports
        .into_iter()
        .filter(|port| {
            matches!(&port.port_type, SerialPortType::UsbPort(info)
                if info.product.as_deref() == Some(USB_PRODUCT))
        })
        .map(|port| port.port_name)
        .min()
        .with_context(|| format!("no USB serial device named {USB_PRODUCT:?} found"))
}

async fn connected(
    calls: &mut mpsc::Receiver<Call>,
    state: &watch::Sender<Connection>,
    capture: &Arc<Mutex<Capture>>,
) -> Result<()> {
    let ports = tokio::task::spawn_blocking(tokio_serial::available_ports).await??;
    let path = select_port(ports)?;
    // Lock the discovered tty before opening it, across daemon state directories.
    let _pico_lock =
        crate::process_lock(&format!("pico-port-{}", std::fs::metadata(&path)?.rdev()))?;
    let mut port = tokio_serial::new(&path, 115200).open_native_async()?;
    port.set_exclusive(true)?;
    let (mut storage, mut payload, mut wire) =
        ([0; WIRE_CAPACITY], [0; MAX_PAYLOAD], [0; WIRE_CAPACITY]);
    let (mut sender, mut receiver) = serial::split(
        port,
        CONTROL_LINK,
        Buffers {
            receive: &mut storage,
            payload: &mut payload,
            transmit: &mut wire,
        },
    );
    let session = rand::random::<u64>().max(1);
    let mut requests = Requests::<2>::new(NonZeroU64::new(session).unwrap());
    let hello_key = requests.reserve()?.into();
    send(
        &mut sender,
        Request {
            key: hello_key,
            command: Command::Hello,
        },
    )
    .await?;
    let hello = timeout(Duration::from_secs(3), async {
        loop {
            if let Message::Response(response) = receive(&mut receiver, &mut requests).await?
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
    crate::logging::event(format!("Pico discovered at {path}; chip ID {identity}"));
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
            incoming = timeout(Duration::from_secs(4), receive(&mut receiver, &mut requests)) => {
                let message = incoming.context("Pico heartbeat timeout")??;
                observe(&message, session, capture, state)?;
            }
            call = calls.recv() => {
                let Some(call) = call else { bail!("daemon stopped"); };
                if call.generation != generation { let _ = call.reply.send(Err(anyhow::anyhow!("connection changed; command not sent"))); continue; }
                let result = exchange(
                    &mut sender, &mut receiver, &mut requests,
                    call.command, |message| observe(message, session, capture, state),
                ).await;
                next_probe = Instant::now() + Duration::from_secs(1);
                let failed = result.is_err();
                let _ = call.reply.send(result);
                if failed { bail!("command transport failed; outcome uncertain, no replay"); }
            }
            _ = tokio::time::sleep_until(next_probe) => {
                let reply = exchange(
                    &mut sender, &mut receiver, &mut requests,
                    Command::Status, |message| observe(message, session, capture, state),
                ).await?;
                ensure!(matches!(reply, Reply::Status(_)), "Pico rejected presence probe");
                next_probe = Instant::now() + Duration::from_secs(1);
            }
        }
    }
}
async fn worker(
    mut calls: mpsc::Receiver<Call>,
    state: watch::Sender<Connection>,
    capture: Arc<Mutex<Capture>>,
) {
    loop {
        if let Err(error) = connected(&mut calls, &state, &capture).await {
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
            .receive(polis_sbc_controller_embedded_api::UartRx {
                session: 1,
                timestamp_us,
                offset,
                bytes: polis_sbc_controller_embedded_api::Bytes::from_slice(bytes).unwrap(),
            })
            .unwrap();
    }
}

#[cfg(test)]
mod discovery_tests {
    use super::*;
    use tokio_serial::UsbPortInfo;

    fn usb(path: &str, product: Option<&str>, serial: Option<&str>) -> SerialPortInfo {
        SerialPortInfo {
            port_name: path.into(),
            port_type: SerialPortType::UsbPort(UsbPortInfo {
                vid: 1,
                pid: 2,
                serial_number: serial.map(str::to_owned),
                manufacturer: None,
                product: product.map(str::to_owned),
            }),
        }
    }

    #[test]
    fn product_name_accepts_any_serial_including_missing_serial() {
        for serial in [Some("replacement-board"), None] {
            assert_eq!(
                select_port(vec![usb("/dev/ttyACM7", Some(USB_PRODUCT), serial)]).unwrap(),
                "/dev/ttyACM7"
            );
        }
    }

    #[test]
    fn only_exact_usb_product_names_match() {
        let unrelated = vec![
            usb("/dev/ttyACM0", Some("Polis SBC Controller Other"), None),
            usb("/dev/ttyACM1", None, None),
            SerialPortInfo {
                port_name: USB_PRODUCT.into(),
                port_type: SerialPortType::Unknown,
            },
        ];
        assert!(select_port(unrelated.clone()).is_err());
        assert!(select_port(vec![]).is_err());
        let mut ports = unrelated;
        ports.push(usb("/dev/ttyACM2", Some(USB_PRODUCT), None));
        assert_eq!(select_port(ports).unwrap(), "/dev/ttyACM2");
    }

    #[test]
    fn selection_is_stable_and_can_rediscover_a_different_port() {
        let first = usb("/dev/ttyACM0", Some(USB_PRODUCT), Some("one"));
        let second = usb("/dev/ttyACM1", Some(USB_PRODUCT), Some("two"));
        assert_eq!(
            select_port(vec![second.clone(), first.clone()]).unwrap(),
            "/dev/ttyACM0"
        );
        assert_eq!(
            select_port(vec![first, second.clone()]).unwrap(),
            "/dev/ttyACM0"
        );
        assert_eq!(select_port(vec![second]).unwrap(), "/dev/ttyACM1");
    }
}

#[cfg(test)]
mod keepalive_tests {
    use super::*;
    use polis_control_link_embassy::stream as embedded;
    use polis_sbc_controller_embedded_api::{BootMode, Counters, ErrorCode, Response};
    use tokio::io::{AsyncReadExt, AsyncWriteExt};

    // Run the real Embassy codec/runtime over an in-memory I/O backend, opposite
    // the real Tokio adapter. The firmware build checks real RP2350/CDC types;
    // physical USB behavior still requires hardware qualification.
    struct Embedded<T>(T);
    impl<T> embedded_io_async::ErrorType for Embedded<T> {
        type Error = embedded_io_async::ErrorKind;
    }
    impl<T: AsyncRead + Unpin> embedded_io_async::Read for Embedded<T> {
        async fn read(&mut self, bytes: &mut [u8]) -> std::result::Result<usize, Self::Error> {
            self.0
                .read(bytes)
                .await
                .map_err(|_| embedded_io_async::ErrorKind::Other)
        }
    }
    impl<T: AsyncWrite + Unpin> embedded_io_async::Write for Embedded<T> {
        async fn write(&mut self, bytes: &[u8]) -> std::result::Result<usize, Self::Error> {
            self.0
                .write(bytes)
                .await
                .map_err(|_| embedded_io_async::ErrorKind::Other)
        }
        async fn flush(&mut self) -> std::result::Result<(), Self::Error> {
            self.0
                .flush()
                .await
                .map_err(|_| embedded_io_async::ErrorKind::Other)
        }
    }
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
    async fn reply<W: embedded_io_async::Write>(
        sender: &mut embedded::Sender<'_, W>,
        key: Key,
        reply: Reply,
    ) {
        let mut body = [0; MAX_BODY];
        sender
            .send(
                Message::Response(Response { key, reply })
                    .to_link(&mut body)
                    .unwrap(),
            )
            .await
            .unwrap();
    }
    async fn request<R: embedded_io_async::Read>(
        receiver: &mut embedded::Receiver<'_, R>,
    ) -> Request {
        let Message::Request(request) =
            receiver.receive(Message::from_link).await.unwrap().unwrap()
        else {
            panic!("expected request")
        };
        request
    }
    fn requests() -> Requests<2> {
        let mut requests = Requests::new(NonZeroU64::new(7).unwrap());
        // Model an already-completed Hello without replacing the tracker.
        let hello = requests.reserve().unwrap();
        requests.cancel(hello).unwrap();
        requests
    }

    #[tokio::test(start_paused = true)]
    async fn long_cycle_keeps_presence_alive_without_repeating_power_operation() {
        let (host, pico) = tokio::io::duplex(4096);
        let server = tokio::spawn(async move {
            let (reader, writer) = tokio::io::split(pico);
            let (mut rx, mut payload, mut tx) =
                ([0; WIRE_CAPACITY], [0; MAX_PAYLOAD], [0; WIRE_CAPACITY]);
            let (mut sender, mut receiver) = embedded::split(
                CONTROL_LINK,
                Embedded(reader),
                Embedded(writer),
                Buffers {
                    receive: &mut rx,
                    payload: &mut payload,
                    transmit: &mut tx,
                },
            );
            let first = request(&mut receiver).await;
            assert_eq!(first.command, Command::CyclePower { off_ms: 4000 });
            reply(&mut sender, first.key, Reply::Accepted).await;
            for expected_id in 3..=5 {
                let probe = request(&mut receiver).await;
                assert_eq!(probe.command, Command::Status);
                assert_eq!(
                    probe.key,
                    Key {
                        session: 7,
                        id: expected_id
                    }
                );
                reply(&mut sender, probe.key, Reply::Status(status())).await;
            }
            reply(&mut sender, first.key, Reply::Completed).await;
        });
        let (reader, writer) = tokio::io::split(host);
        let (mut rx, mut payload, mut tx) =
            ([0; WIRE_CAPACITY], [0; MAX_PAYLOAD], [0; WIRE_CAPACITY]);
        let (mut sender, mut receiver) = polis_control_link_tokio::stream::split(
            CONTROL_LINK,
            reader,
            writer,
            Buffers {
                receive: &mut rx,
                payload: &mut payload,
                transmit: &mut tx,
            },
        );
        let mut requests = requests();
        let mut observations = 0;
        let result = exchange(
            &mut sender,
            &mut receiver,
            &mut requests,
            Command::CyclePower { off_ms: 4000 },
            |_| {
                observations += 1;
                Ok(())
            },
        )
        .await
        .unwrap();
        assert_eq!(result, Reply::Completed);
        assert_eq!(requests.pending_count(), 0);
        assert_eq!(observations, 5);
        server.await.unwrap();
    }

    #[tokio::test(start_paused = true)]
    async fn rejected_probe_stops_pending_operation_without_replay() {
        let (host, pico) = tokio::io::duplex(4096);
        let server = tokio::spawn(async move {
            let (reader, writer) = tokio::io::split(pico);
            let (mut rx, mut payload, mut tx) =
                ([0; WIRE_CAPACITY], [0; MAX_PAYLOAD], [0; WIRE_CAPACITY]);
            let (mut sender, mut receiver) = embedded::split(
                CONTROL_LINK,
                Embedded(reader),
                Embedded(writer),
                Buffers {
                    receive: &mut rx,
                    payload: &mut payload,
                    transmit: &mut tx,
                },
            );
            let first = request(&mut receiver).await;
            reply(&mut sender, first.key, Reply::Accepted).await;
            let probe = request(&mut receiver).await;
            assert_eq!(probe.command, Command::Status);
            reply(&mut sender, probe.key, Reply::Error(ErrorCode::NeedHello)).await;
            assert!(matches!(
                receiver.receive(Message::from_link).await,
                Err(polis_control_link_embassy::Error::Closed)
            ));
        });
        let (reader, writer) = tokio::io::split(host);
        let (mut rx, mut payload, mut tx) =
            ([0; WIRE_CAPACITY], [0; MAX_PAYLOAD], [0; WIRE_CAPACITY]);
        let (mut sender, mut receiver) = polis_control_link_tokio::stream::split(
            CONTROL_LINK,
            reader,
            writer,
            Buffers {
                receive: &mut rx,
                payload: &mut payload,
                transmit: &mut tx,
            },
        );
        let mut requests = requests();
        let error = exchange(
            &mut sender,
            &mut receiver,
            &mut requests,
            Command::Boot {
                mode: BootMode::Recovery,
                off_ms: 4000,
            },
            |_| Ok(()),
        )
        .await
        .unwrap_err();
        assert!(error.to_string().contains("presence probe"));
        drop(sender);
        drop(receiver);
        server.await.unwrap();
    }

    #[tokio::test(start_paused = true)]
    async fn completion_retires_pending_probe_and_ignores_its_late_reply() {
        let (host, pico) = tokio::io::duplex(4096);
        let server = tokio::spawn(async move {
            let (reader, writer) = tokio::io::split(pico);
            let (mut rx, mut payload, mut tx) =
                ([0; WIRE_CAPACITY], [0; MAX_PAYLOAD], [0; WIRE_CAPACITY]);
            let (mut sender, mut receiver) = embedded::split(
                CONTROL_LINK,
                Embedded(reader),
                Embedded(writer),
                Buffers {
                    receive: &mut rx,
                    payload: &mut payload,
                    transmit: &mut tx,
                },
            );
            let first = request(&mut receiver).await;
            reply(&mut sender, first.key, Reply::Accepted).await;
            let probe = request(&mut receiver).await;
            reply(&mut sender, first.key, Reply::Completed).await;
            reply(&mut sender, probe.key, Reply::Status(status())).await;
            let mut body = [0; MAX_BODY];
            // Stale event is ignored; the following live event survives buffered input.
            for session in [6, 7] {
                sender
                    .send(
                        Message::Heartbeat(polis_sbc_controller_embedded_api::Heartbeat {
                            session,
                            boot_id: 8,
                            status: status(),
                        })
                        .to_link(&mut body)
                        .unwrap(),
                    )
                    .await
                    .unwrap();
            }
        });
        let (reader, writer) = tokio::io::split(host);
        let (mut rx, mut payload, mut tx) =
            ([0; WIRE_CAPACITY], [0; MAX_PAYLOAD], [0; WIRE_CAPACITY]);
        let (mut sender, mut receiver) = polis_control_link_tokio::stream::split(
            CONTROL_LINK,
            reader,
            writer,
            Buffers {
                receive: &mut rx,
                payload: &mut payload,
                transmit: &mut tx,
            },
        );
        let mut requests = requests();
        assert_eq!(
            exchange(
                &mut sender,
                &mut receiver,
                &mut requests,
                Command::CyclePower { off_ms: 4000 },
                |_| Ok(())
            )
            .await
            .unwrap(),
            Reply::Completed
        );
        assert_eq!(requests.pending_count(), 0);
        assert!(
            matches!(receive(&mut receiver, &mut requests).await.unwrap(), Message::Heartbeat(value) if value.session == 7 && value.boot_id == 8)
        );
        server.await.unwrap();
    }

    #[tokio::test(start_paused = true)]
    async fn timed_out_send_retires_sender_without_replay() {
        let (host, mut pico) = tokio::io::duplex(1);
        let (mut payload, mut tx) = ([0; MAX_PAYLOAD], [0; WIRE_CAPACITY]);
        let mut sender = Sender::new(CONTROL_LINK, host, &mut payload, &mut tx);
        let request = Request {
            key: Key { session: 7, id: 1 },
            command: Command::SetPower { on: true },
        };
        assert!(
            send(&mut sender, request.clone())
                .await
                .unwrap_err()
                .to_string()
                .contains("outcome uncertain")
        );
        assert!(
            send(&mut sender, request)
                .await
                .unwrap_err()
                .to_string()
                .contains("interrupted")
        );
        drop(sender);
        let mut bytes = Vec::new();
        pico.read_to_end(&mut bytes).await.unwrap();
        assert_eq!(bytes.len(), 1);
    }
}
