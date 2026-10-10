use crate::{
    board::{USB_PACKET, UsbDriver},
    shared::*,
};
use core::sync::atomic::Ordering;
use embassy_futures::select::{Either3, select3};
use embassy_time::{Duration, with_timeout};
use embassy_usb::{
    Handler, UsbDevice,
    class::cdc_acm::{Receiver, Sender},
};
use polis_wired_framing::Decoder;
use polis_wired_protocol::{ErrorCode, MAX_PAYLOAD, Message, Reply, Response, WIRE_CAPACITY};

pub struct LinkHandler;
impl Handler for LinkHandler {
    fn enabled(&mut self, enabled: bool) {
        if !enabled {
            EPOCH.fetch_add(1, Ordering::Relaxed);
        }
    }

    fn reset(&mut self) {
        EPOCH.fetch_add(1, Ordering::Relaxed);
    }
    fn configured(&mut self, configured: bool) {
        if !configured {
            EPOCH.fetch_add(1, Ordering::Relaxed);
        }
    }
}

#[embassy_executor::task]
pub async fn run(mut usb: UsbDevice<'static, UsbDriver>) -> ! {
    usb.run().await
}

#[embassy_executor::task]
pub async fn receive(mut receiver: Receiver<'static, UsbDriver>) -> ! {
    let mut storage = [0; WIRE_CAPACITY];
    let mut decoder = Decoder::new(&mut storage);
    let mut packet = [0; USB_PACKET];
    let mut current_epoch = epoch();
    loop {
        receiver.wait_connection().await;
        match receiver.read_packet(&mut packet).await {
            Ok(len) => {
                let new_epoch = epoch();
                if new_epoch != current_epoch {
                    decoder.reset();
                    current_epoch = new_epoch;
                }
                decoder.feed(&packet[..len], |frame| {
                    let frame = match frame {
                        Ok(frame) => frame,
                        Err(_) => {
                            count(|c| c.framing_errors = c.framing_errors.saturating_add(1));
                            return;
                        }
                    };
                    match Message::decode(frame) {
                        Ok(Message::Request(request)) => {
                            usb_activity();
                            let key = request.key;
                            if REQUESTS.try_send((current_epoch, request)).is_err() {
                                count(|c| {
                                    c.request_queue_full = c.request_queue_full.saturating_add(1)
                                });
                                respond(
                                    current_epoch,
                                    Message::Response(Response {
                                        key,
                                        reply: Reply::Error(ErrorCode::Busy),
                                    }),
                                );
                            }
                        }
                        _ => count(|c| c.protocol_errors = c.protocol_errors.saturating_add(1)),
                    }
                });
            }
            Err(_) => decoder.reset(),
        }
        embassy_futures::yield_now().await;
    }
}

#[embassy_executor::task]
pub async fn transmit(mut sender: Sender<'static, UsbDriver>) -> ! {
    let mut scratch = [0; MAX_PAYLOAD as usize];
    let mut wire = [0; WIRE_CAPACITY];
    loop {
        // Select is biased in this order: command responses, heartbeat, UART.
        let (generation, message) =
            match select3(RESPONSES.receive(), HEARTBEAT.wait(), UART_RX.receive()).await {
                Either3::First(value) => value,
                Either3::Second((generation, value)) => (generation, Message::Heartbeat(value)),
                Either3::Third((generation, value)) => (generation, Message::UartRx(value)),
            };
        let rx_len = match &message {
            Message::UartRx(value) => value.bytes.len(),
            _ => 0,
        };
        if generation != epoch() {
            lost(rx_len);
            continue;
        }
        let encoded = match message.encode(&mut scratch, &mut wire) {
            Ok(bytes) => bytes,
            Err(_) => {
                count(|c| c.protocol_errors = c.protocol_errors.saturating_add(1));
                lost(rx_len);
                continue;
            }
        };
        let sent = with_timeout(Duration::from_millis(500), async {
            sender.wait_connection().await;
            for packet in encoded.chunks(USB_PACKET) {
                if generation != epoch() {
                    return false;
                }
                if sender.write_packet(packet).await.is_err() {
                    return false;
                }
            }
            // Terminate exact multiples so the host does not wait for another frame.
            if encoded.len() % USB_PACKET == 0 && sender.write_packet(&[]).await.is_err() {
                return false;
            }
            generation == epoch()
        })
        .await;
        if sent != Ok(true) {
            count(|c| c.usb_tx_failures = c.usb_tx_failures.saturating_add(1));
            lost(rx_len);
        } else {
            usb_activity();
        }
    }
}
