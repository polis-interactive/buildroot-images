// SPDX-License-Identifier: GPL-2.0-only
//! Private bindings adapter for the pinned Raspberry Pi 6.12 kernel.
//!
//! Layouts and function declarations come from kernel bindgen, never handwritten
//! C struct replicas. Raw pointers remain here and in the callback trampoline.

use crate::{
    lifecycle::{self, Mode, Slots, MAX_OUTPUTS},
    render,
};
use core::{mem::MaybeUninit, ptr, slice};
use kernel::{
    bindings,
    error::{code::*, from_err_ptr, Error, Result},
};

static LOGO: &[u8; render::LOGO_BYTES] = include_bytes!("polis-logo.rgb");
// drm_fourcc.h: fourcc_code('X', 'R', '2', '4'), little-endian XRGB8888.
const XRGB8888: u32 = u32::from_le_bytes(*b"XR24");

fn status(value: i32) -> Result {
    if value < 0 {
        Err(Error::from_errno(value))
    } else {
        Ok(())
    }
}

/// Owns a client buffer; the enclosing callback state outlives every Buffer.
pub(crate) struct Buffer(*mut bindings::drm_client_buffer);

impl Drop for Buffer {
    fn drop(&mut self) {
        // SAFETY: Unique ownership, unmapped, client still live. The lifecycle
        // detaches desired modeset pointers and retains active old scanout until
        // a successful replacement commit (or driver unregister).
        unsafe {
            bindings::drm_client_framebuffer_delete(self.0);
        }
    }
}

struct Mapping<'a>(&'a mut Buffer);
impl Drop for Mapping<'_> {
    fn drop(&mut self) {
        // SAFETY: Created only after successful vmap; one unmap per mapping.
        unsafe {
            bindings::drm_client_buffer_vunmap(self.0 .0);
        }
    }
}

struct Modesets(*mut bindings::mutex);
impl Drop for Modesets {
    fn drop(&mut self) {
        // SAFETY: Constructed with this initialized mutex held by this thread.
        unsafe {
            bindings::mutex_unlock(self.0);
        }
    }
}

pub(crate) struct Display {
    client: *mut bindings::drm_client_dev,
    acquired: bool,
}

impl Display {
    /// # Safety
    /// Caller holds clientlist_mutex for our registered client and must keep its
    /// allocation/internal DRM file alive through this adapter and its buffers.
    pub(crate) unsafe fn new(client: *mut bindings::drm_client_dev) -> Self {
        Self {
            client,
            acquired: false,
        }
    }

    fn lock_modesets(&self) -> Modesets {
        // SAFETY: new() guarantees a live initialized client at a stable address.
        let mutex = unsafe { ptr::addr_of_mut!((*self.client).modeset_mutex) };
        // SAFETY: Lock balanced by the guard. No recursive modeset locking.
        unsafe {
            bindings::mutex_lock(mutex);
        }
        Modesets(mutex)
    }
}

impl lifecycle::Display for Display {
    type Buffer = Buffer;
    type Error = Error;

    fn acquire(&mut self) -> bool {
        if self.acquired {
            return true;
        }
        // SAFETY: Live client retains dev. Success holds master_mutex; the
        // lifecycle's Master guard releases it, excluding application takeover.
        self.acquired = unsafe { bindings::drm_master_internal_acquire((*self.client).dev) };
        self.acquired
    }

    fn release(&mut self) {
        if !self.acquired {
            return;
        }
        // SAFETY: Only called by Master after a successful acquire.
        unsafe {
            bindings::drm_master_internal_release((*self.client).dev);
        }
        self.acquired = false;
    }

    fn probe(&mut self) -> Result<Slots<Mode>> {
        // SAFETY: Live client; API handles its own modeset mutex. Bound preferred
        // modes to 4096x2160; unsupported modes fail without changing scanout.
        status(unsafe {
            bindings::drm_client_modeset_probe(
                self.client,
                render::MAX_WIDTH as u32,
                render::MAX_HEIGHT as u32,
            )
        })?;
        let _lock = self.lock_modesets();
        let mut modes = lifecycle::empty();
        for (slot, output) in modes.iter_mut().enumerate() {
            // SAFETY: drm_client_init allocates num_crtc+1 entries terminated by
            // a NULL crtc; iterate only through that sentinel, with mutex held.
            let set = unsafe { &*(*self.client).modesets.add(slot) };
            if set.crtc.is_null() {
                return Ok(modes);
            }
            if !set.mode.is_null() && set.num_connectors > 0 {
                // SAFETY: probe owns each mode until next probe/release; locked.
                let mode = unsafe { &*set.mode };
                let width = u32::from(mode.hdisplay);
                let height = u32::from(mode.vdisplay);
                render::layout(width as usize, height as usize, width as usize * 4)
                    .ok_or(EINVAL)?;
                *output = Some(Mode { width, height });
            }
        }
        // SAFETY: We saw MAX_OUTPUTS non-null entries, so the sentinel/allocation
        // contains at least one more entry. Reject more CRTCs than our storage.
        if unsafe { (*(*self.client).modesets.add(MAX_OUTPUTS)).crtc.is_null() } {
            Ok(modes)
        } else {
            Err(E2BIG)
        }
    }

    fn draw(&mut self, mode: Mode) -> Result<Buffer> {
        // SAFETY: Live client, validated mode, supported DRM format. The helper
        // returns ERR_PTR on failure and a uniquely owned buffer on success.
        let raw = from_err_ptr(unsafe {
            bindings::drm_client_framebuffer_create(self.client, mode.width, mode.height, XRGB8888)
        })?;
        if raw.is_null() {
            return Err(ENOMEM);
        }
        let mut buffer = Buffer(raw);
        // SAFETY: create initializes pitch and GEM storage covering the image.
        let pitch = unsafe { (*raw).pitch as usize };
        let size =
            render::layout(mode.width as usize, mode.height as usize, pitch).ok_or(EINVAL)?;
        let mut map = MaybeUninit::<bindings::iosys_map>::uninit();
        // SAFETY: A fresh buffer with no existing mapping; out-pointer valid.
        status(unsafe { bindings::drm_client_buffer_vmap(raw, map.as_mut_ptr()) })?;
        let mapping = Mapping(&mut buffer);
        // SAFETY: Successful vmap initialized every field in the returned map.
        let map = unsafe { map.assume_init() };
        if map.is_iomem {
            return Err(Error::from_errno(-(bindings::EOPNOTSUPP as i32)));
        }
        // SAFETY: !is_iomem selects vaddr in the generated anonymous union. VC4
        // DMA buffers are CPU-addressable; reject other mappings explicitly.
        let address = unsafe { map.__bindgen_anon_1.vaddr.cast::<u8>() };
        if address.is_null() {
            return Err(EFAULT);
        }
        // SAFETY: Fresh dumb buffer has pitch*height bytes. It has not been
        // published to scanout, has no aliases, and remains pinned/mapped until
        // Mapping drops. layout bounds the slice below isize::MAX.
        let pixels = unsafe { slice::from_raw_parts_mut(address, size) };
        render::paint(
            pixels,
            mode.width as usize,
            mode.height as usize,
            pitch,
            LOGO,
        )
        .ok_or(EINVAL)?;
        drop(mapping);
        // SAFETY: Buffer remains live; NULL rect flushes the whole image.
        status(unsafe { bindings::drm_client_framebuffer_flush(raw, ptr::null_mut()) })?;
        Ok(buffer)
    }

    fn configure(&mut self, buffers: &Slots<Buffer>) {
        let _lock = self.lock_modesets();
        // Clear *all* entries, even if probe rejected an unexpected CRTC count.
        let mut index = 0;
        loop {
            // SAFETY: Same sentinel-terminated array as probe, locked. Raw field
            // access avoids creating Rust references to C-owned mutable state.
            let set = unsafe { (*self.client).modesets.add(index) };
            if unsafe { (*set).crtc.is_null() } {
                break;
            }
            let buffer = buffers.get(index).and_then(Option::as_ref);
            // SAFETY: Buffer keeps fb alive until configure clears/replaces this
            // pointer; release the mutex before calling the commit helper.
            unsafe {
                (*set).fb = buffer.map_or(ptr::null_mut(), |buffer| (*buffer.0).fb);
                (*set).x = 0;
                (*set).y = 0;
            }
            index += 1;
        }
    }

    fn commit(&mut self) -> Result {
        if !self.acquired {
            return Err(EINVAL);
        }
        // SAFETY: Master is held, modeset_mutex is not. The helper acquires that
        // mutex itself and performs a blocking atomic commit on VC4.
        status(unsafe { bindings::drm_client_modeset_commit_locked(self.client) })
    }
}
