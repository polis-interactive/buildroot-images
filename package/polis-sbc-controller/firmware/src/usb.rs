use crate::shared::*;
use core::{cell::Cell, sync::atomic::Ordering};
use embassy_futures::select::{Either3, select3};
use embedded_io_async::{Read, Write};
use polis_control_link_embassy::{
    Error, supervisor,
    usb::{Disconnect, Lifecycle},
};
use polis_sbc_controller_embedded_api::{ErrorCode, MAX_BODY, Message, Reply, Response};

// Cancellation by USB reset must count an in-flight console chunk as lost too.
struct PendingFrame {
    uart_bytes: usize,
    completed: bool,
}
impl Drop for PendingFrame {
    fn drop(&mut self) {
        if !self.completed {
            count(|c| c.usb_tx_failures = c.usb_tx_failures.saturating_add(1));
            lost(self.uart_bytes);
        }
    }
}

// Application queues, generation filtering and loss accounting stay here.
pub struct Application {
    generation: Cell<u32>,
}
impl Application {
    pub const fn new() -> Self {
        Self {
            generation: Cell::new(0),
        }
    }
}
impl Lifecycle for Application {
    fn reset(&self) {
        EPOCH.fetch_add(1, Ordering::Relaxed);
    }
    fn connected(&self) {
        self.generation.set(epoch());
    }
    fn disconnected(&self, reason: Disconnect) {
        defmt::debug!("Control link retired: {:?}", defmt::Debug2Format(&reason));
        if epoch() == self.generation.get() {
            EPOCH.fetch_add(1, Ordering::Relaxed);
        }
    }
}
impl<R: Read, W: Write> supervisor::ConnectionHandler<R, W> for Application {
    async fn receive(&self, mut receiver: supervisor::Receiver<'_, '_, R>) {
        let generation = self.generation.get();
        loop {
            match receiver.receive(Message::from_link).await {
                Ok(Ok(Message::Request(request))) if generation == epoch() => {
                    usb_activity();
                    let key = request.key;
                    if REQUESTS.try_send((generation, request)).is_err() {
                        count(|c| c.request_queue_full = c.request_queue_full.saturating_add(1));
                        respond(
                            generation,
                            Message::Response(Response {
                                key,
                                reply: Reply::Error(ErrorCode::Busy),
                            }),
                        );
                    }
                }
                Err(Error::Framing(_)) => {
                    count(|c| c.framing_errors = c.framing_errors.saturating_add(1))
                }
                Err(Error::Protocol(_)) | Ok(_) => {
                    count(|c| c.protocol_errors = c.protocol_errors.saturating_add(1))
                }
                Err(_) => return,
            }
            embassy_futures::yield_now().await;
        }
    }
    async fn transmit(&self, mut sender: supervisor::Sender<'_, '_, W>) {
        let generation = self.generation.get();
        let mut body = [0; MAX_BODY];
        loop {
            // Preserve priority: command responses, heartbeat, then console traffic.
            let (message_epoch, message) =
                match select3(RESPONSES.receive(), HEARTBEAT.wait(), UART_RX.receive()).await {
                    Either3::First(value) => value,
                    Either3::Second((epoch, value)) => (epoch, Message::Heartbeat(value)),
                    Either3::Third((epoch, value)) => (epoch, Message::UartRx(value)),
                };
            let uart_bytes = match &message {
                Message::UartRx(value) => value.bytes.len(),
                _ => 0,
            };
            if message_epoch != generation || generation != epoch() {
                lost(uart_bytes);
                continue;
            }
            let encoded = match message.to_link(&mut body) {
                Ok(message) => message,
                Err(_) => {
                    count(|c| c.protocol_errors = c.protocol_errors.saturating_add(1));
                    lost(uart_bytes);
                    continue;
                }
            };
            let mut pending = PendingFrame {
                uart_bytes,
                completed: false,
            };
            if sender.send(encoded).await.is_err() {
                return;
            }
            pending.completed = true;
            usb_activity();
            embassy_futures::yield_now().await;
        }
    }
}
