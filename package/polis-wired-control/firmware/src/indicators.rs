use crate::{board, indicator_state::ActivityPulse, shared};
use core::sync::atomic::Ordering;
use cyw43_pio::{PioSpi, RM2_CLOCK_DIVIDER};
use embassy_executor::Spawner;
use embassy_rp::{
    dma,
    gpio::{Level, Output},
    peripherals::PIO0,
    pio::Pio,
};
use embassy_time::{Duration, Instant, Timer, with_timeout};
use static_cell::StaticCell;

#[embassy_executor::task]
pub async fn activity(r: board::ActivityLeds) -> ! {
    let mut daemon_led = Output::new(r.daemon, Level::Low);
    let mut uart_led = Output::new(r.uart, Level::Low);
    let mut daemon = ActivityPulse::default();
    let mut uart = ActivityPulse::default();
    loop {
        let now = Instant::now().as_millis();
        daemon_led.set_level(Level::from(
            daemon.update(now, shared::USB_ACTIVITY.swap(false, Ordering::Relaxed)),
        ));
        uart_led.set_level(Level::from(
            uart.update(now, shared::UART_ACTIVITY.swap(false, Ordering::Relaxed)),
        ));
        Timer::after_millis(10).await;
    }
}

#[embassy_executor::task]
async fn cyw43_runner(
    runner: cyw43::Runner<'static, cyw43::SpiBus<Output<'static>, PioSpi<'static, PIO0, 0>>>,
) -> ! {
    runner.run().await
}

/// The Pico 2 W LED is CYW43 GPIO0, not RP2350 GP25. Follow Embassy's
/// rp235x/blinky_wifi example with the published 0.7/0.10 driver APIs.
/// Initialization and LED writes never hold up the power/UART device loop.
#[embassy_executor::task]
pub async fn onboard(spawner: Spawner, r: board::OnboardLed) {
    let power = Output::new(r.power, Level::Low);
    let cs = Output::new(r.chip_select, Level::High);
    let mut pio = Pio::new(r.pio, board::Irqs);
    let spi = PioSpi::new(
        &mut pio.common,
        pio.sm0,
        RM2_CLOCK_DIVIDER,
        pio.irq0,
        cs,
        r.data,
        r.clock,
        dma::Channel::new(r.dma, board::Irqs),
    );
    static STATE: StaticCell<cyw43::State> = StaticCell::new();
    let init = cyw43::new(
        STATE.init(cyw43::State::new()),
        power,
        spi,
        cyw43::aligned_bytes!("../vendor/cyw43/43439A0.bin"),
        cyw43::aligned_bytes!("../vendor/cyw43/nvram_rp2040.bin"),
    );
    let Ok((_network, mut control, runner)) = with_timeout(Duration::from_secs(10), init).await
    else {
        defmt::warn!("Onboard LED CYW43 initialization timed out");
        return;
    };
    spawner.spawn(cyw43_runner(runner).unwrap());
    if with_timeout(
        Duration::from_secs(10),
        control.init(cyw43::aligned_bytes!("../vendor/cyw43/43439A0_clm.bin")),
    )
    .await
    .is_err()
    {
        defmt::warn!("Onboard LED CYW43 control initialization timed out");
        return;
    }
    // No network stack, association, or Bluetooth is started.
    let mut previous = None;
    loop {
        let now = Instant::now().as_millis();
        let on = shared::daemon_connected(now) || (now / 500).is_multiple_of(2);
        if previous != Some(on) {
            if with_timeout(Duration::from_millis(250), control.gpio_set(0, on))
                .await
                .is_err()
            {
                defmt::warn!("Onboard LED update timed out");
                return;
            }
            previous = Some(on);
        }
        Timer::after_millis(50).await;
    }
}
