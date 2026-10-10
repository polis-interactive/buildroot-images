//! Hardware-independent daemon presence and LED timing, tested on the host.

pub const DAEMON_TIMEOUT_MS: u64 = 4_000;

#[derive(Default)]
pub struct DaemonPresence {
    seen: Option<(u32, u64)>,
}
impl DaemonPresence {
    pub const fn new() -> Self {
        Self { seen: None }
    }
    pub fn refresh(&mut self, epoch: u32, now_ms: u64) {
        self.seen = Some((epoch, now_ms));
    }
    pub fn connected(&self, epoch: u32, now_ms: u64) -> bool {
        self.seen.is_some_and(|(seen_epoch, seen_ms)| {
            seen_epoch == epoch && now_ms.saturating_sub(seen_ms) < DAEMON_TIMEOUT_MS
        })
    }
}

#[derive(Default)]
pub struct ActivityPulse {
    on: bool,
    pending: bool,
    deadline_ms: u64,
}
impl ActivityPulse {
    /// Coalesce traffic into visible 100 ms pulses with at least 50 ms dark
    /// between pulses, so continuous traffic blinks instead of becoming solid.
    pub fn update(&mut self, now_ms: u64, activity: bool) -> bool {
        self.pending |= activity;
        if self.on && now_ms >= self.deadline_ms {
            self.on = false;
            self.deadline_ms = now_ms + 50;
        } else if !self.on && self.pending && now_ms >= self.deadline_ms {
            self.on = true;
            self.pending = false;
            self.deadline_ms = now_ms + 100;
        }
        self.on
    }
}
