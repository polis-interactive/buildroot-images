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
use polis_control_link_embassy::{Buffers, usb as control_usb};
use polis_sbc_controller_embedded_api::{CONTROL_LINK, MAX_PAYLOAD, USB_PRODUCT, WIRE_CAPACITY};
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
    config.product = Some(USB_PRODUCT);
    config.serial_number = Some(serial.as_str());
    config.max_power = 100;
    config.max_packet_size_0 = 64;
    static CONFIG: StaticCell<[u8; 256]> = StaticCell::new();
    static BOS: StaticCell<[u8; 256]> = StaticCell::new();
    static CONTROL: StaticCell<[u8; 64]> = StaticCell::new();
    let application = usb::Application::new();
    let usb_state = control_usb::State::new();
    let mut events = usb_state.handler(&application);
    let mut cdc = State::new();
    let driver = embassy_rp::usb::Driver::new(board.usb, board::Irqs);
    let mut builder = embassy_usb::Builder::new(
        driver,
        config,
        CONFIG.init([0; 256]),
        BOS.init([0; 256]),
        &mut [],
        CONTROL.init([0; 64]),
    );
    builder.handler(&mut events);
    let class = CdcAcmClass::new(&mut builder, &mut cdc, board::USB_PACKET as u16);
    let (mut receive, mut payload, mut transmit, mut packet) = (
        [0; WIRE_CAPACITY],
        [0; MAX_PAYLOAD],
        [0; WIRE_CAPACITY],
        [0; board::USB_PACKET],
    );
    let mut runner = control_usb::Runner::new(
        builder.build(),
        class,
        &usb_state,
        &application,
        control_usb::Config {
            link: CONTROL_LINK,
            send_timeout: embassy_time::Duration::from_millis(500),
            reconnect_delay: embassy_time::Duration::from_millis(1),
        },
        control_usb::Storage {
            frames: Buffers {
                receive: &mut receive,
                payload: &mut payload,
                transmit: &mut transmit,
            },
            packet: &mut packet,
        },
    )
    .unwrap();
    embassy_futures::join::join(
        runner.run(),
        device::run(
            board.uart,
            board.power,
            board.recovery,
            board.watchdog,
            board.device_id,
            board.boot_id,
        ),
    )
    .await;
}
