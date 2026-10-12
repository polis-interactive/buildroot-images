//! One clock for the physical enable write and unbuffered kernel-entry marker.
use crate::Key;
use serde::{Deserialize, Serialize};

pub const ENTRY_MARKER: &[u8] = b"\nPLIS_ENTRY_V1\n";

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct BootTiming {
    pub key: Option<Key>,
    /// Bounds bracketing the GPIO enable write, not a scheduled deadline.
    pub enable_before_us: u64,
    pub enable_after_us: u64,
    /// Batch observation time of the marker's last byte (not its wire edge).
    pub entry_observed_us: Option<u64>,
    pub entry_count: u32,
}

#[derive(Default)]
pub struct Recorder {
    timing: BootTiming,
    matched: usize,
}
impl Recorder {
    pub fn power_on(&mut self, key: Key, before: u64, after: u64) {
        self.timing = BootTiming {
            key: Some(key),
            enable_before_us: before,
            enable_after_us: after,
            ..Default::default()
        };
        self.matched = 0;
    }
    pub fn snapshot(&self) -> BootTiming {
        self.timing
    }
    pub fn receive(&mut self, bytes: &[u8], observed_us: u64) {
        if self.timing.key.is_none() || observed_us < self.timing.enable_after_us {
            return;
        }
        for &byte in bytes {
            if byte == ENTRY_MARKER[self.matched] {
                self.matched += 1;
                if self.matched == ENTRY_MARKER.len() {
                    self.timing.entry_count = self.timing.entry_count.saturating_add(1);
                    self.timing.entry_observed_us.get_or_insert(observed_us);
                    // The final newline can also start the next marker.
                    self.matched = 1;
                }
            } else {
                self.matched = usize::from(byte == ENTRY_MARKER[0]);
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn split_marker_and_clock_origin() {
        for split in 0..=ENTRY_MARKER.len() {
            let mut r = Recorder::default();
            r.receive(ENTRY_MARKER, 10);
            assert_eq!(r.snapshot().entry_count, 0);
            r.power_on(Key { session: 5, id: 8 }, 100, 102);
            r.receive(ENTRY_MARKER, 99);
            r.receive(&ENTRY_MARKER[..split], 200);
            r.receive(&ENTRY_MARKER[split..], 205);
            let s = r.snapshot();
            assert_eq!(s.entry_count, 1);
            assert_eq!(
                s.entry_observed_us,
                Some(if split == ENTRY_MARKER.len() {
                    200
                } else {
                    205
                })
            );
            assert_eq!((s.enable_before_us, s.enable_after_us), (100, 102));
        }
    }
    #[test]
    fn reboot_resets_partial_marker_and_duplicate_is_visible() {
        let mut r = Recorder::default();
        let key = Key { session: 5, id: 9 };
        r.power_on(key, 1, 2);
        r.receive(&ENTRY_MARKER[..5], 10);
        r.power_on(Key { id: 10, ..key }, 20, 21);
        r.receive(&ENTRY_MARKER[5..], 25);
        assert_eq!(r.snapshot().entry_count, 0);
        r.receive(ENTRY_MARKER, 30);
        r.receive(ENTRY_MARKER, 40);
        assert_eq!(r.snapshot().entry_count, 2);
        assert_eq!(r.snapshot().entry_observed_us, Some(30));
    }
}
