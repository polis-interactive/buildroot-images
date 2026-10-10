#![no_std]
#![no_main]

mod board;
mod device;
mod indicator_state;
mod indicators;
mod shared;
mod usb;

use core::fmt::Write;
use embassy_executor::Spawner;
use embassy_usb::class::cdc_acm::{CdcAcmClass, State};
use static_cell::StaticCell;
use {defmt_rtt as _, panic_probe as _};

#[embassy_executor::main(
    executor = "embassy_rp::executor::Executor",
    entry = "cortex_m_rt::entry"
)]
async fn main(spawner: Spawner) {
    let board = board::init();
    spawner.spawn(indicators::activity(board.activity).unwrap());
    spawner.spawn(indicators::onboard(spawner, board.onboard).unwrap());
    static SERIAL: StaticCell<heapless::String<16>> = StaticCell::new();
    let serial = SERIAL.init(heapless::String::new());
    for byte in board.device_id {
        write!(serial, "{byte:02x}").unwrap();
    }
    let mut config = embassy_usb::Config::new(board::USB_VID, board::USB_PID);
    config.manufacturer = Some("Polis");
    config.product = Some("Polis Wired Control");
    config.serial_number = Some(serial.as_str());
    config.max_power = 100;
    config.max_packet_size_0 = 64;
    static CONFIG: StaticCell<[u8; 256]> = StaticCell::new();
    static BOS: StaticCell<[u8; 256]> = StaticCell::new();
    static CONTROL: StaticCell<[u8; 64]> = StaticCell::new();
    static CDC: StaticCell<State<'static>> = StaticCell::new();
    static HANDLER: StaticCell<usb::LinkHandler> = StaticCell::new();
    let driver = embassy_rp::usb::Driver::new(board.usb, board::Irqs);
    let mut builder = embassy_usb::Builder::new(
        driver,
        config,
        CONFIG.init([0; 256]),
        BOS.init([0; 256]),
        &mut [],
        CONTROL.init([0; 64]),
    );
    builder.handler(HANDLER.init(usb::LinkHandler));
    let class = CdcAcmClass::new(
        &mut builder,
        CDC.init(State::new()),
        board::USB_PACKET as u16,
    );
    let (sender, receiver) = class.split();
    spawner.spawn(usb::run(builder.build()).unwrap());
    spawner.spawn(usb::receive(receiver).unwrap());
    spawner.spawn(usb::transmit(sender).unwrap());
    device::run(
        board.uart,
        board.power,
        board.recovery,
        board.watchdog,
        board.device_id,
        board.boot_id,
    )
    .await;
}
