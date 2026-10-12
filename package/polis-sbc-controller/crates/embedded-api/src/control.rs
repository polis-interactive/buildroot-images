//! Executor-independent session policy and product power state, tested on the host.
use crate::{BootMode, Cycle, ErrorCode, Key, MAX_OFF_MS, MIN_OFF_MS};
use polis_control_link_protocol::{RequestGate, StateError};

/// Hello policy is product-owned; replay protection comes from control-link.
#[derive(Default)]
pub struct Session {
    gate: Option<RequestGate>,
    nonce: u64,
}
impl Session {
    pub fn nonce(&self) -> u64 {
        self.nonce
    }

    pub fn hello(&mut self, key: Key) -> Result<(), ErrorCode> {
        let link_key: polis_control_link_protocol::Key =
            key.try_into().map_err(|_| ErrorCode::InvalidRequest)?;
        if self.nonce != key.session {
            self.gate = Some(RequestGate::new(link_key.session));
            self.nonce = key.session;
        }
        self.accept(key)
    }

    pub fn accept(&mut self, key: Key) -> Result<(), ErrorCode> {
        let gate = self.gate.as_mut().ok_or(ErrorCode::NeedHello)?;
        let key = key.try_into().map_err(|_| ErrorCode::InvalidRequest)?;
        gate.accept(key).map_err(|error| match error {
            StateError::WrongSession => ErrorCode::WrongSession,
            StateError::DuplicateRequest => ErrorCode::DuplicateRequest,
            _ => ErrorCode::InvalidRequest,
        })
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
