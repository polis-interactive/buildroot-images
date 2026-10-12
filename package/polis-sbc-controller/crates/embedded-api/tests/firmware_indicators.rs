// Exercise the exact portable state machines used by the firmware without
// linking the RP2350 HAL into a native test binary.
#[path = "../../../firmware/src/indicator_state.rs"]
mod indicator_state;
use indicator_state::{ActivityPulse, DAEMON_TIMEOUT_MS, DaemonPresence};
use polis_sbc_controller_embedded_api::{BootMode, Key, control::Power};

#[test]
fn daemon_timeout_cuts_power_and_keepalive_does_not_restore_it() {
    let mut presence = DaemonPresence::new();
    let mut power = Power::default();
    assert!(!power.enforce_daemon(presence.connected(1, 0)));
    presence.refresh(1, 100);
    power.set(true).unwrap();
    assert!(!power.enforce_daemon(presence.connected(1, 100 + DAEMON_TIMEOUT_MS - 1)));
    assert!(power.is_on());
    assert!(power.enforce_daemon(presence.connected(1, 100 + DAEMON_TIMEOUT_MS)));
    assert!(!power.is_on());
    assert!(!power.enforce_daemon(false));
    presence.refresh(1, 5_000);
    assert!(!power.enforce_daemon(presence.connected(1, 5_000)));
    assert!(!power.is_on());
    power.set(true).unwrap();
    assert!(power.is_on());
}

#[test]
fn usb_disconnect_cuts_power_without_waiting_for_timeout() {
    let mut presence = DaemonPresence::new();
    presence.refresh(1, 100);
    let mut power = Power::default();
    power.set(true).unwrap();
    assert!(power.enforce_daemon(presence.connected(2, 101)));
    assert!(!power.is_on());
}

#[test]
fn disconnect_cancels_cycles_even_at_the_power_on_deadline() {
    for mode in [BootMode::Normal, BootMode::Recovery] {
        for disconnected_epoch in [1, 2] {
            let mut presence = DaemonPresence::new();
            presence.refresh(1, 0);
            let mut power = Power::default();
            power
                .start_boot(Key { session: 1, id: 2 }, 0, 4_000, mode)
                .unwrap();
            // Epoch 2 models USB loss before the deadline; epoch 1 models
            // daemon silence expiring exactly when the cycle would power on.
            let now_ms = if disconnected_epoch == 2 { 1 } else { 4_000 };
            assert!(power.enforce_daemon(presence.connected(disconnected_epoch, now_ms)));
            assert_eq!(power.cycle(), None);
            assert_eq!(power.mode(), mode);
            presence.refresh(disconnected_epoch, 5_000);
            assert!(!power.enforce_daemon(presence.connected(disconnected_epoch, 5_000)));
            assert_eq!(power.advance(60_000_000), None);
            assert!(!power.is_on());
        }
    }
}

#[test]
fn keepalives_allow_long_cycles_to_finish() {
    let mut presence = DaemonPresence::new();
    let mut power = Power::default();
    let key = Key { session: 1, id: 2 };
    power.start_cycle(key, 0, 60_000).unwrap();
    for second in 0..=60 {
        let now_ms = second * 1_000;
        presence.refresh(1, now_ms);
        assert!(!power.enforce_daemon(presence.connected(1, now_ms)));
        assert_eq!(power.advance(now_ms * 1_000), (second == 60).then_some(key));
    }
    assert!(power.is_on());
}

#[test]
fn presence_requires_a_request_and_expires_when_daemon_stops() {
    let mut presence = DaemonPresence::new();
    assert!(!presence.connected(1, 0));
    presence.refresh(1, 100);
    assert!(presence.connected(1, 100 + DAEMON_TIMEOUT_MS - 1));
    assert!(!presence.connected(1, 100 + DAEMON_TIMEOUT_MS));
    presence.refresh(1, 5_000);
    assert!(presence.connected(1, 5_000));
}

#[test]
fn reconnect_requires_a_request_in_the_new_usb_epoch() {
    let mut presence = DaemonPresence::new();
    presence.refresh(1, 10);
    assert!(!presence.connected(2, 11));
    presence.refresh(2, 12);
    assert!(presence.connected(2, 12));
}

#[test]
fn isolated_traffic_is_visible_then_goes_dark() {
    let mut pulse = ActivityPulse::default();
    assert!(!pulse.update(0, false));
    assert!(pulse.update(1, true));
    assert!(pulse.update(100, false));
    assert!(!pulse.update(101, false));
    assert!(!pulse.update(1_000, false));
}

#[test]
fn continuous_and_bursty_traffic_preserves_visible_dark_intervals() {
    let mut pulse = ActivityPulse::default();
    assert!(pulse.update(0, true));
    assert!(pulse.update(99, true));
    assert!(!pulse.update(100, true));
    assert!(!pulse.update(149, true));
    assert!(pulse.update(150, false));
    assert!(!pulse.update(250, false));
    assert!(!pulse.update(300, false));
    assert!(pulse.update(310, true));
}
