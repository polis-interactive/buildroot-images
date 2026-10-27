// SPDX-License-Identifier: GPL-2.0-only
//! Display policy shared by the kernel client and host failure-injection tests.

// VC4 has fewer CRTCs; bound stack storage and reject incompatible drivers.
pub(crate) const MAX_OUTPUTS: usize = 8;
pub(crate) type Slots<T> = [Option<T>; MAX_OUTPUTS];

#[derive(Clone, Copy, Debug)]
pub(crate) struct Mode {
    pub(crate) width: u32,
    pub(crate) height: u32,
}

pub(crate) fn empty<T>() -> Slots<T> {
    core::array::from_fn(|_| None)
}

/// Implementations serialize callbacks and exclude userspace modesets while
/// acquired. Buffer deletion must remain valid until after `Screen` is dropped.
pub(crate) trait Display {
    type Buffer;
    type Error;
    fn acquire(&mut self) -> bool;
    fn release(&mut self);
    fn probe(&mut self) -> Result<Slots<Mode>, Self::Error>;
    fn draw(&mut self, mode: Mode) -> Result<Self::Buffer, Self::Error>;
    fn configure(&mut self, buffers: &Slots<Self::Buffer>);
    fn commit(&mut self) -> Result<(), Self::Error>;
}

struct Master<'a, D: Display>(&'a mut D);
impl<D: Display> Drop for Master<'_, D> {
    fn drop(&mut self) {
        self.0.release();
    }
}

pub(crate) struct Screen<B> {
    buffers: Slots<B>,
}

impl<B> Screen<B> {
    pub(crate) fn new() -> Self {
        Self { buffers: empty() }
    }

    /// Busy is an ordinary skipped redraw. Retry at the next hotplug/lastclose.
    pub(crate) fn refresh<D: Display<Buffer = B>>(
        &mut self,
        display: &mut D,
    ) -> Result<(), D::Error> {
        if !display.acquire() {
            return Ok(());
        }
        let master = Master(display);
        let modes = master.0.probe()?;
        let mut next = empty();
        for (slot, mode) in modes.into_iter().enumerate() {
            if let Some(mode) = mode {
                next[slot] = Some(master.0.draw(mode)?);
            }
        }
        master.0.configure(&next);
        if let Err(error) = master.0.commit() {
            // The old buffers may still be scanned out. Retain them, and remove
            // every borrowed pointer to the candidate buffers before deleting.
            master.0.configure(&empty());
            return Err(error);
        }
        // The blocking atomic commit has replaced scanout; now old buffers can
        // be deleted. This also releases them on a successful disconnect.
        self.buffers = next;
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{cell::RefCell, rc::Rc};

    struct Buffer(usize, Rc<RefCell<Vec<String>>>);
    impl Drop for Buffer {
        fn drop(&mut self) {
            self.1.borrow_mut().push(format!("delete {}", self.0));
        }
    }
    struct Fake {
        log: Rc<RefCell<Vec<String>>>,
        busy: bool,
        connected: usize,
        fail_draw: bool,
        fail_commit: bool,
        fail_probe: bool,
        next: usize,
    }
    impl Fake {
        fn new() -> Self {
            Self {
                log: Rc::default(),
                busy: false,
                connected: 0,
                fail_draw: false,
                fail_commit: false,
                fail_probe: false,
                next: 0,
            }
        }
        fn event(&self, text: &str) {
            self.log.borrow_mut().push(text.into());
        }
        fn take(&self) -> Vec<String> {
            core::mem::take(&mut *self.log.borrow_mut())
        }
    }
    impl Display for Fake {
        type Buffer = Buffer;
        type Error = ();
        fn acquire(&mut self) -> bool {
            self.event("acquire");
            !self.busy
        }
        fn release(&mut self) {
            self.event("release");
        }
        fn probe(&mut self) -> Result<Slots<Mode>, ()> {
            self.event("probe");
            if self.fail_probe {
                return Err(());
            }
            let mut modes = empty();
            for mode in &mut modes[..self.connected] {
                *mode = Some(Mode {
                    width: 1920,
                    height: 1080,
                });
            }
            Ok(modes)
        }
        fn draw(&mut self, mode: Mode) -> Result<Buffer, ()> {
            assert_eq!((mode.width, mode.height), (1920, 1080));
            if self.fail_draw {
                return Err(());
            }
            self.next += 1;
            Ok(Buffer(self.next, self.log.clone()))
        }
        fn configure(&mut self, buffers: &Slots<Buffer>) {
            self.event(&format!("configure {}", buffers.iter().flatten().count()));
        }
        fn commit(&mut self) -> Result<(), ()> {
            self.event("commit");
            if self.fail_commit {
                Err(())
            } else {
                Ok(())
            }
        }
    }

    #[test]
    fn headless_boot_hotplug_takeover_restore_and_disconnect() {
        let mut fake = Fake::new();
        let mut screen = Screen::new();
        screen.refresh(&mut fake).unwrap();
        assert_eq!(
            fake.take(),
            ["acquire", "probe", "configure 0", "commit", "release"]
        );
        fake.connected = 2;
        screen.refresh(&mut fake).unwrap();
        assert_eq!(
            fake.take(),
            ["acquire", "probe", "configure 2", "commit", "release"]
        );
        fake.busy = true;
        screen.refresh(&mut fake).unwrap();
        assert_eq!(fake.take(), ["acquire"]);
        fake.busy = false;
        fake.connected = 1; // monitor changed while app owned DRM
        screen.refresh(&mut fake).unwrap();
        assert_eq!(
            fake.take(),
            [
                "acquire",
                "probe",
                "configure 1",
                "commit",
                "delete 1",
                "delete 2",
                "release"
            ]
        );
        fake.connected = 0;
        screen.refresh(&mut fake).unwrap();
        assert_eq!(
            fake.take(),
            [
                "acquire",
                "probe",
                "configure 0",
                "commit",
                "delete 3",
                "release"
            ]
        );
    }

    #[test]
    fn failed_commit_keeps_old_scanout_and_detaches_candidates_before_drop() {
        let mut fake = Fake::new();
        fake.connected = 1;
        let mut screen = Screen::new();
        screen.refresh(&mut fake).unwrap();
        fake.take();
        fake.fail_commit = true;
        assert!(screen.refresh(&mut fake).is_err());
        assert_eq!(
            fake.take(),
            [
                "acquire",
                "probe",
                "configure 1",
                "commit",
                "configure 0",
                "delete 2",
                "release"
            ]
        );
        fake.fail_commit = false;
        screen.refresh(&mut fake).unwrap();
        assert_eq!(
            fake.take(),
            [
                "acquire",
                "probe",
                "configure 1",
                "commit",
                "delete 1",
                "release"
            ]
        );
        drop(screen);
        assert_eq!(fake.take(), ["delete 3"]);
    }

    #[test]
    fn probe_and_allocation_failures_release_master_and_allow_retry() {
        let mut fake = Fake::new();
        let mut screen = Screen::new();
        fake.connected = 1;
        fake.fail_probe = true;
        assert!(screen.refresh(&mut fake).is_err());
        assert_eq!(fake.take(), ["acquire", "probe", "release"]);
        fake.fail_probe = false;
        fake.fail_draw = true;
        assert!(screen.refresh(&mut fake).is_err());
        assert_eq!(fake.take(), ["acquire", "probe", "release"]);
        fake.fail_draw = false;
        screen.refresh(&mut fake).unwrap();
    }
}
