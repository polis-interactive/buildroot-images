//! Executor-independent session and power state, tested on the host.
use crate::{BootMode, Cycle, ErrorCode, Key, MAX_OFF_MS, MIN_OFF_MS};

#[derive(Default)]
pub struct Session {
    nonce: u64,
    last_id: u32,
}

impl Session {
    pub fn nonce(&self) -> u64 {
        self.nonce
    }

    /// A Hello with the same nonce does not reset the request high-water mark.
    pub fn hello(&mut self, key: Key) -> Result<(), ErrorCode> {
        if key.session == 0 || key.id == 0 {
            return Err(ErrorCode::InvalidRequest);
        }
        if self.nonce == key.session {
            return self.accept(key);
        }
        self.nonce = key.session;
        self.last_id = key.id;
        Ok(())
    }

    pub fn accept(&mut self, key: Key) -> Result<(), ErrorCode> {
        if self.nonce == 0 {
            return Err(ErrorCode::NeedHello);
        }
        if self.nonce != key.session {
            return Err(ErrorCode::WrongSession);
        }
        if key.id <= self.last_id {
            return Err(ErrorCode::DuplicateRequest);
        }
        self.last_id = key.id;
        Ok(())
    }
}

#[derive(Default)]
pub struct Power {
    mode: BootMode,
    on: bool,
    cycle: Option<Cycle>,
}

impl Power {
    pub fn mode(&self) -> BootMode {
        self.mode
    }

    pub fn select_recovery(&mut self, enabled: bool) -> Result<(), ErrorCode> {
        if self.on || self.cycle.is_some() {
            return Err(ErrorCode::Busy);
        }
        self.mode = if enabled {
            BootMode::Recovery
        } else {
            BootMode::Normal
        };
        Ok(())
    }

    pub fn start_boot(
        &mut self,
        key: Key,
        now_us: u64,
        off_ms: u32,
        mode: BootMode,
    ) -> Result<(), ErrorCode> {
        self.start_cycle(key, now_us, off_ms)?;
        self.mode = mode;
        self.cycle.as_mut().unwrap().boot_mode = mode;
        Ok(())
    }

    pub fn is_on(&self) -> bool {
        self.on
    }
    pub fn cycle(&self) -> Option<Cycle> {
        self.cycle
    }

    /// Losing daemon presence overrides even an active cycle. Return whether
    /// the controller must disable the power output and abort UART transmission.
    pub fn enforce_daemon(&mut self, connected: bool) -> bool {
        if connected || (!self.on && self.cycle.is_none()) {
            return false;
        }
        self.on = false;
        self.cycle = None;
        true
    }

    /// An active cycle owns the output until its deadline unless daemon
    /// presence is lost. Subsequent power commands cannot shorten it.
    pub fn set(&mut self, on: bool) -> Result<(), ErrorCode> {
        if self.cycle.is_some() {
            return Err(ErrorCode::Busy);
        }
        self.on = on;
        Ok(())
    }

    pub fn start_cycle(&mut self, key: Key, now_us: u64, off_ms: u32) -> Result<(), ErrorCode> {
        if !(MIN_OFF_MS..=MAX_OFF_MS).contains(&off_ms) {
            return Err(ErrorCode::InvalidRequest);
        }
        if self.cycle.is_some() {
            return Err(ErrorCode::Busy);
        }
        let power_on_at_us = now_us
            .checked_add(u64::from(off_ms) * 1000)
            .ok_or(ErrorCode::InvalidRequest)?;
        self.on = false;
        self.cycle = Some(Cycle {
            boot_mode: self.mode,
            key,
            power_on_at_us,
        });
        Ok(())
    }

    pub fn advance(&mut self, now_us: u64) -> Option<Key> {
        let cycle = self.cycle?;
        if now_us < cycle.power_on_at_us {
            return None;
        }
        self.cycle = None;
        self.on = true;
        Some(cycle.key)
    }
}
