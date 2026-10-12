//! The fixture's only board-specific choices. GP numbers are Pico GPIOs.
use assign_resources::assign_resources;
use core::sync::atomic::{AtomicU8, Ordering};
use embassy_rp::{
    Peri, bind_interrupts,
    gpio::{Level, Output, Pin},
    peripherals::{self, DMA_CH0, PIO0, UART0, USB},
    trng::Trng,
    uart::{BufferedUart, Config},
    watchdog::Watchdog,
};
use polis_sbc_controller_embedded_api::DEFAULT_BAUD;
use static_cell::StaticCell;

// All fixture and onboard radio pin assignments live here.
assign_resources! {
    controls: Controls {
        recovery: PIN_14,  // physical 19: external open-drain gate
        power: PIN_15,     // physical 20: active-high 5 V switch enable
        power_led: PIN_16, // physical 21: commanded Pi power
    }
    activity: ActivityLeds {
        daemon: PIN_17,    // physical 22
        uart: PIN_18,      // physical 24
    }
    uart: ConsoleUart {
        peripheral: UART0,
        tx: PIN_0,
        rx: PIN_1,
    }
    onboard: OnboardLed {
        power: PIN_23,
        chip_select: PIN_25,
        data: PIN_24,
        clock: PIN_29,
        pio: PIO0,
        dma: DMA_CH0,
    }
    system: SystemResources {
        usb: USB,
        trng: TRNG,
        watchdog: WATCHDOG,
    }
}

// Bench-only USB identifiers, not an assigned product VID/PID.
pub const USB_VID: u16 = 0xc0de;
pub const USB_PID: u16 = 0xcafe;
pub const USB_PACKET: usize = 64;

bind_interrupts!(pub struct Irqs {
    USBCTRL_IRQ => embassy_rp::usb::InterruptHandler<USB>;
    UART0_IRQ => embassy_rp::uart::BufferedInterruptHandler<UART0>;
    TRNG_IRQ => embassy_rp::trng::InterruptHandler<embassy_rp::peripherals::TRNG>;
    PIO0_IRQ_0 => embassy_rp::pio::InterruptHandler<PIO0>;
    DMA_IRQ_0 => embassy_rp::dma::InterruptHandler<DMA_CH0>;
});

pub struct Board {
    pub usb: Peri<'static, USB>,
    pub uart: BufferedUart,
    pub power: PiPower,
    pub recovery: Output<'static>,
    pub activity: ActivityLeds,
    pub onboard: OnboardLed,
    pub watchdog: Watchdog,
    pub device_id: [u8; 8],
    pub boot_id: u64,
}

/// Keep the power indicator synchronous with every actual switch command.
/// It reports commanded power, not a measured Pi rail voltage.
pub struct PiPower {
    enable: Output<'static>,
    led: Output<'static>,
}
impl PiPower {
    pub fn set_high(&mut self) {
        self.enable.set_high();
        self.led.set_high();
    }
    pub fn set_low(&mut self) {
        self.enable.set_low();
        self.led.set_low();
    }
}

static UART_TX_GPIO: AtomicU8 = AtomicU8::new(0);

/// Gate just the UART TX pad's output enable. UART retains ownership of its pin;
/// touching OEOVER avoids aliasing a second HAL pin driver. RX has no pull-up.
/// The index comes from the assigned TX resource before the HAL takes ownership.
pub fn drive_uart_tx(enabled: bool) {
    embassy_rp::pac::IO_BANK0
        .gpio(UART_TX_GPIO.load(Ordering::Relaxed) as usize)
        .ctrl()
        .modify(|w| {
            w.set_oeover(if enabled {
                embassy_rp::pac::io::vals::Oeover::NORMAL
            } else {
                embassy_rp::pac::io::vals::Oeover::DISABLE
            });
        });
}

pub fn init() -> Board {
    let p = embassy_rp::init(Default::default());
    let r = split_resources!(p);
    // Active-high enable; the external switch must also default OFF at reset.
    let power = PiPower {
        enable: Output::new(r.controls.power, Level::Low),
        led: Output::new(r.controls.power_led, Level::Low),
    };
    // High enables an EXTERNAL open-drain pull-down on Pi BCM GPIO8.
    // Low releases it; use an external gate pull-down for reset-time safety.
    let recovery = Output::new(r.controls.recovery, Level::Low);
    let device_id = embassy_rp::otp::get_chipid().unwrap().to_be_bytes();
    let mut trng = Trng::new(r.system.trng, Irqs, Default::default());
    let boot_id = trng.blocking_next_u64();
    static TX: StaticCell<[u8; 256]> = StaticCell::new();
    static RX: StaticCell<[u8; 2048]> = StaticCell::new();
    let mut config = Config::default();
    config.baudrate = DEFAULT_BAUD;
    UART_TX_GPIO.store(r.uart.tx.pin(), Ordering::Relaxed);
    let uart = BufferedUart::new(
        r.uart.peripheral,
        r.uart.tx,
        r.uart.rx,
        Irqs,
        TX.init([0; 256]),
        RX.init([0; 2048]),
        config,
    );
    drive_uart_tx(false);
    let mut watchdog = Watchdog::new(r.system.watchdog);
    watchdog.start(embassy_time::Duration::from_secs(2));
    Board {
        usb: r.system.usb,
        uart,
        power,
        recovery,
        activity: r.activity,
        onboard: r.onboard,
        watchdog,
        device_id,
        boot_id,
    }
}
