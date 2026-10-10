use crate::{board, shared::*};
use core::future::{Future, ready};
use embassy_futures::select::{Either, Either3, select, select3};
use embassy_rp::{
    gpio::Output,
    uart::{BufferedUart, Error as UartError},
    watchdog::Watchdog,
};
use embassy_time::{Duration, Instant, Timer};
use embedded_io_async::{Read, Write};
use polis_wired_protocol::{
    control::{Power, Session},
    *,
};

struct PendingTx {
    epoch: u32,
    key: Key,
    bytes: Bytes,
    written: usize,
    deadline_us: u64,
}

struct Device {
    epoch: u32,
    session: Session,
    power: Power,
    cycle_epoch: u32,
    timing: timing::Recorder,
    baud: u32,
    rx_offset: u64,
    pending: Option<PendingTx>,
    // On abort, buffered bytes drain with the TX pad disabled. Never restore
    // the pad or change baud while the old bytes remain in software/hardware.
    draining: bool,
    device_id: [u8; 8],
    boot_id: u64,
}

impl Device {
    fn status(&self) -> Status {
        Status {
            timing: self.timing.snapshot(),
            boot_mode: self.power.mode(),
            uptime_us: Instant::now().as_micros(),
            power_on: self.power.is_on(),
            cycle: self.power.cycle(),
            uart_baud: self.baud,
            uart_tx_busy: self.pending.is_some() || self.draining,
            uart_rx_offset: self.rx_offset,
            counters: counters(),
        }
    }

    fn reply(&self, key: Key, reply: Reply) {
        respond(self.epoch, Message::Response(Response { key, reply }));
    }

    fn abort_tx(&mut self, reason: ErrorCode) {
        if let Some(tx) = self.pending.take() {
            board::drive_uart_tx(false);
            self.draining = true;
            respond(
                tx.epoch,
                Message::Response(Response {
                    key: tx.key,
                    reply: Reply::Error(reason),
                }),
            );
        }
    }

    fn sync_link(&mut self, pin: &mut board::PiPower) {
        let current = epoch();
        // Check before processing requests as well as before advancing cycles:
        // a late keepalive must not hide an expired presence deadline.
        let connected = self.epoch == current && daemon_connected(Instant::now().as_millis());
        if self.power.enforce_daemon(connected) {
            board::drive_uart_tx(false);
            pin.set_low();
            self.abort_tx(ErrorCode::Aborted);
        }
        if self.epoch != current {
            self.abort_tx(ErrorCode::Aborted);
            self.epoch = current;
            self.session = Session::default();
        }
    }

    fn request(
        &mut self,
        request: Request,
        uart: &mut BufferedUart,
        pin: &mut board::PiPower,
        recovery_pin: &mut Output<'static>,
    ) {
        let key = request.key;
        let valid = request.validate().and_then(|()| {
            if request.command == Command::Hello {
                self.session.hello(key)
            } else {
                self.session.accept(key)
            }
        });
        if let Err(error) = valid {
            self.reply(key, Reply::Error(error));
            return;
        }
        daemon_seen();
        let result = match request.command {
            Command::Hello => {
                // New ownership cannot continue a previous caller's write.
                self.abort_tx(ErrorCode::Aborted);
                while let Ok((_, data)) = UART_RX.try_receive() {
                    dropped(data.bytes.len());
                }
                Ok(Reply::Hello {
                    device_id: self.device_id,
                    boot_id: self.boot_id,
                    firmware: [0, 2, 0],
                    max_uart_chunk: UART_CHUNK as u16,
                    status: self.status(),
                })
            }
            Command::Status => Ok(Reply::Status(self.status())),
            Command::SetRecovery { enabled } => self.power.select_recovery(enabled).map(|()| {
                recovery_pin.set_level(if enabled {
                    embassy_rp::gpio::Level::High
                } else {
                    embassy_rp::gpio::Level::Low
                });
                Reply::Completed
            }),
            Command::Boot { mode, off_ms } => {
                if self.power.cycle().is_some() {
                    Err(ErrorCode::Busy)
                } else {
                    board::drive_uart_tx(false);
                    pin.set_low();
                    self.abort_tx(ErrorCode::Aborted);
                    self.power
                        .start_boot(key, Instant::now().as_micros(), off_ms, mode)
                        .map(|()| {
                            recovery_pin.set_level(if mode == BootMode::Recovery {
                                embassy_rp::gpio::Level::High
                            } else {
                                embassy_rp::gpio::Level::Low
                            });
                            self.cycle_epoch = self.epoch;
                            Reply::Accepted
                        })
                }
            }
            Command::SetPower { on } => {
                if on && self.draining {
                    Err(ErrorCode::Busy)
                } else {
                    let was_on = self.power.is_on();
                    self.power.set(on).map(|()| {
                        if on {
                            if !was_on {
                                self.enable_power(pin, key);
                            }
                            board::drive_uart_tx(true);
                        } else {
                            board::drive_uart_tx(false);
                            pin.set_low();
                            self.abort_tx(ErrorCode::Aborted);
                        }
                        Reply::Completed
                    })
                }
            }
            Command::CyclePower { off_ms } => {
                if self.power.cycle().is_some() {
                    Err(ErrorCode::Busy)
                } else {
                    board::drive_uart_tx(false);
                    pin.set_low();
                    self.abort_tx(ErrorCode::Aborted);
                    self.power
                        .start_cycle(key, Instant::now().as_micros(), off_ms)
                        .map(|()| {
                            self.cycle_epoch = self.epoch;
                            Reply::Accepted
                        })
                }
            }
            Command::ConfigureUart { baud } => {
                if self.power.is_on() || self.power.cycle().is_some() || self.draining {
                    Err(ErrorCode::Busy)
                } else {
                    uart.set_baudrate(baud);
                    self.baud = baud;
                    Ok(Reply::Completed)
                }
            }
            Command::WriteUart { bytes } => {
                if !self.power.is_on() {
                    Err(ErrorCode::PowerOff)
                } else if self.pending.is_some() || self.draining {
                    Err(ErrorCode::Busy)
                } else {
                    self.pending = Some(PendingTx {
                        epoch: self.epoch,
                        key,
                        bytes,
                        written: 0,
                        deadline_us: Instant::now().as_micros() + 1_000_000,
                    });
                    Ok(Reply::Accepted)
                }
            }
        };
        self.reply(key, result.unwrap_or_else(Reply::Error));
    }

    async fn tick(&mut self, uart: &mut BufferedUart, pin: &mut board::PiPower) {
        if let Some(tx) = &mut self.pending {
            let mut failed = Instant::now().as_micros() >= tx.deadline_us;
            if !failed && tx.written < tx.bytes.len() {
                match poll_once(uart.write(&tx.bytes[tx.written..])).await {
                    Some(Ok(n)) => tx.written += n,
                    Some(Err(_)) => failed = true,
                    None => {}
                }
            }
            if failed {
                count(|c| c.uart_errors = c.uart_errors.saturating_add(1));
                self.abort_tx(ErrorCode::UartError);
            } else if tx.written == tx.bytes.len()
                && matches!(poll_once(uart.flush()).await, Some(Ok(())))
                && !uart.busy()
            {
                let tx = self.pending.take().unwrap();
                uart_activity();
                respond(
                    tx.epoch,
                    Message::Response(Response {
                        key: tx.key,
                        reply: Reply::Completed,
                    }),
                );
            }
        }
        if self.draining && matches!(poll_once(uart.flush()).await, Some(Ok(()))) && !uart.busy() {
            self.draining = false;
            board::drive_uart_tx(self.power.is_on());
        }
        // A cycle's off interval is a minimum: drain any aborted UART bytes
        // before enabling TX again. No USB operation is awaited here.
        if !self.draining
            && let Some(key) = self.power.advance(Instant::now().as_micros())
        {
            self.enable_power(pin, key);
            board::drive_uart_tx(true);
            respond(
                self.cycle_epoch,
                Message::Response(Response {
                    key,
                    reply: Reply::Completed,
                }),
            );
        }
    }

    fn enable_power(&mut self, pin: &mut board::PiPower, key: Key) {
        let before = Instant::now().as_micros();
        pin.set_high();
        let after = Instant::now().as_micros();
        self.timing.power_on(key, before, after);
    }

    fn received(&mut self, bytes: &[u8]) {
        if !bytes.is_empty() {
            uart_activity();
        }
        let offset = self.rx_offset;
        self.rx_offset = self.rx_offset.saturating_add(bytes.len() as u64);
        if self.session.nonce() == 0 || !self.power.is_on() {
            dropped(bytes.len());
            return;
        }
        let timestamp_us = Instant::now().as_micros();
        self.timing.receive(bytes, timestamp_us);
        let data = UartRx {
            session: self.session.nonce(),
            timestamp_us,
            offset,
            bytes: Bytes::from_slice(bytes).unwrap(),
        };
        if UART_RX.try_send((self.epoch, data)).is_err() {
            lost(bytes.len());
        }
    }
}

/// Poll a cancellation-safe buffered UART operation once, without holding up
/// power deadlines, USB request processing, or RX draining.
async fn poll_once<F: Future>(future: F) -> Option<F::Output> {
    match select(future, ready(())).await {
        Either::First(result) => Some(result),
        Either::Second(()) => None,
    }
}

pub async fn run(
    mut uart: BufferedUart,
    mut power_pin: board::PiPower,
    mut recovery_pin: Output<'static>,
    mut watchdog: Watchdog,
    device_id: [u8; 8],
    boot_id: u64,
) -> ! {
    let mut device = Device {
        epoch: epoch(),
        session: Session::default(),
        power: Power::default(),
        cycle_epoch: epoch(),
        timing: timing::Recorder::default(),
        baud: DEFAULT_BAUD,
        rx_offset: 0,
        pending: None,
        draining: false,
        device_id,
        boot_id,
    };
    let mut rx = [0; UART_CHUNK];
    let mut next_tick = Instant::now() + Duration::from_millis(1);
    let mut next_heartbeat = Instant::now() + Duration::from_secs(1);
    loop {
        device.sync_link(&mut power_pin);
        match select3(Timer::at(next_tick), REQUESTS.receive(), uart.read(&mut rx)).await {
            Either3::First(()) => {
                device.sync_link(&mut power_pin);
                device.tick(&mut uart, &mut power_pin).await;
                watchdog.feed(Duration::from_secs(2));
                next_tick = Instant::now() + Duration::from_millis(1);
                if Instant::now() >= next_heartbeat {
                    if device.session.nonce() != 0 {
                        HEARTBEAT.signal((
                            device.epoch,
                            Heartbeat {
                                session: device.session.nonce(),
                                boot_id,
                                status: device.status(),
                            },
                        ));
                    }
                    next_heartbeat = Instant::now() + Duration::from_secs(1);
                }
            }
            Either3::Second((generation, request)) => {
                device.sync_link(&mut power_pin);
                if generation == device.epoch {
                    device.request(request, &mut uart, &mut power_pin, &mut recovery_pin);
                }
            }
            Either3::Third(Ok(n)) => {
                device.sync_link(&mut power_pin);
                device.received(&rx[..n]);
            }
            Either3::Third(Err(error)) => count(|c| {
                c.uart_errors = c.uart_errors.saturating_add(1);
                if matches!(error, UartError::Overrun) {
                    c.uart_overruns = c.uart_overruns.saturating_add(1);
                }
            }),
        }
        // Ready UART reads/requests must not monopolize Embassy's executor.
        embassy_futures::yield_now().await;
    }
}
