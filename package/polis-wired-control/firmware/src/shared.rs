use core::cell::RefCell;
use core::sync::atomic::{AtomicBool, AtomicU32, Ordering};
use embassy_sync::{
    blocking_mutex::{Mutex, raw::CriticalSectionRawMutex},
    channel::Channel,
    signal::Signal,
};
use polis_wired_protocol::{Counters, Heartbeat, Message, Request, UartRx};

pub type RawMutex = CriticalSectionRawMutex;
pub static EPOCH: AtomicU32 = AtomicU32::new(1);
pub static USB_ACTIVITY: AtomicBool = AtomicBool::new(false);
pub static UART_ACTIVITY: AtomicBool = AtomicBool::new(false);
static DAEMON: Mutex<RawMutex, RefCell<crate::indicator_state::DaemonPresence>> =
    Mutex::new(RefCell::new(crate::indicator_state::DaemonPresence::new()));

pub fn daemon_seen() {
    DAEMON.lock(|d| {
        d.borrow_mut()
            .refresh(epoch(), embassy_time::Instant::now().as_millis())
    });
}
pub fn daemon_connected(now_ms: u64) -> bool {
    DAEMON.lock(|d| d.borrow().connected(epoch(), now_ms))
}
pub fn uart_activity() {
    UART_ACTIVITY.store(true, Ordering::Relaxed);
}
pub fn usb_activity() {
    USB_ACTIVITY.store(true, Ordering::Relaxed);
}
pub static REQUESTS: Channel<RawMutex, (u32, Request), 8> = Channel::new();
pub static RESPONSES: Channel<RawMutex, (u32, Message), 8> = Channel::new();
pub static UART_RX: Channel<RawMutex, (u32, UartRx), 16> = Channel::new();
pub static HEARTBEAT: Signal<RawMutex, (u32, Heartbeat)> = Signal::new();
static COUNTERS: Mutex<RawMutex, RefCell<Counters>> = Mutex::new(RefCell::new(Counters {
    framing_errors: 0,
    protocol_errors: 0,
    request_queue_full: 0,
    response_drops: 0,
    uart_errors: 0,
    uart_overruns: 0,
    uart_rx_dropped_bytes: 0,
    uart_rx_lost_bytes: 0,
    usb_tx_failures: 0,
}));

pub fn epoch() -> u32 {
    EPOCH.load(Ordering::Relaxed)
}
pub fn count(update: impl FnOnce(&mut Counters)) {
    COUNTERS.lock(|c| update(&mut c.borrow_mut()));
}
pub fn counters() -> Counters {
    COUNTERS.lock(|c| *c.borrow())
}
pub fn respond(epoch: u32, message: Message) {
    if RESPONSES.try_send((epoch, message)).is_err() {
        count(|c| c.response_drops = c.response_drops.saturating_add(1));
    }
}
pub fn dropped(bytes: usize) {
    count(|c| c.uart_rx_dropped_bytes = c.uart_rx_dropped_bytes.saturating_add(bytes as u64));
}

pub fn lost(bytes: usize) {
    dropped(bytes);
    count(|c| c.uart_rx_lost_bytes = c.uart_rx_lost_bytes.saturating_add(bytes as u64));
}
