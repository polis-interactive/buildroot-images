// SPDX-License-Identifier: GPL-2.0-only
//! Persistent VC4 DRM client for the Polis development image.
//!
//! The only C integration is registration from vc4_bind(). DRM owns the client
//! afterwards; callbacks and destruction are serialized by clientlist_mutex.

#![no_std]

mod drm;
mod lifecycle;
mod render;

use core::{cell::UnsafeCell, ptr};
use kernel::{bindings, c_str, prelude::*, types::Opaque};

const __LOG_PREFIX: &[u8] = b"polis-splash-screen\0";

struct Data {
    screen: lifecycle::Screen<drm::Buffer>,
    last_error: i32,
}

#[repr(C)]
struct Client {
    // Must be first: callbacks receive this address, not a separate allocation.
    raw: Opaque<bindings::drm_client_dev>,
    data: UnsafeCell<Data>,
}

struct Callbacks(bindings::drm_client_funcs);
// SAFETY: Immutable function pointers; owner is NULL because this is built in.
unsafe impl Sync for Callbacks {}

static CALLBACKS: Callbacks = Callbacks(bindings::drm_client_funcs {
    owner: ptr::null_mut(),
    unregister: Some(unregister),
    restore: Some(restore),
    hotplug: Some(hotplug),
});

/// Register exactly once, after successful drm_dev_register().
///
/// # Safety
/// `dev` must be the live, registered VC4 DRM device. Its driver must use atomic
/// modesetting and must not register another kernel display client.
#[no_mangle]
pub unsafe extern "C" fn polis_splash_screen_setup(dev: *mut bindings::drm_device) -> i32 {
    let allocation = match KBox::new(
        Client {
            raw: Opaque::uninit(),
            data: UnsafeCell::new(Data {
                screen: lifecycle::Screen::new(),
                last_error: 0,
            }),
        },
        GFP_KERNEL,
    ) {
        Ok(value) => value,
        Err(_) => return -(bindings::ENOMEM as i32),
    };
    // Transfer ownership to C only after pinning the allocation at its final
    // address. Moving the initialized C mutex/list would invalidate them.
    let state = KBox::into_raw(allocation);
    let client = state.cast::<bindings::drm_client_dev>();
    // SAFETY: Client.raw is at offset zero and holds uninitialized C storage.
    // drm_client_init expects zero-initialized storage and initializes its locks.
    let result = unsafe {
        ptr::write_bytes(client, 0, 1);
        bindings::drm_client_init(
            dev,
            client,
            c_str!("polis-splash-screen").as_char_ptr(),
            &CALLBACKS.0,
        )
    };
    if result != 0 {
        // SAFETY: Initialization failure cleans up its C resources and no
        // callbacks can run before register; reclaim the unique allocation.
        unsafe {
            drop(KBox::from_raw(state));
        }
        return result;
    }
    pr_info!("registered persistent DRM splash client\n");
    // SAFETY: All callback state is initialized and allocation ownership now
    // belongs to DRM. register invokes hotplug synchronously; no access after it.
    unsafe {
        bindings::drm_client_register(client);
    }
    0
}

unsafe fn refresh(client: *mut bindings::drm_client_dev) -> i32 {
    // SAFETY: DRM passes our registered allocation with clientlist_mutex held,
    // excluding other callbacks and unregister. Never borrow the C-owned fields
    // mutably as Rust references; only the UnsafeCell data is uniquely borrowed.
    let data = unsafe { &mut *(*client.cast::<Client>()).data.get() };
    // SAFETY: This callback guarantees the client stays live for the operation.
    let mut display = unsafe { drm::Display::new(client) };
    let result = match data.screen.refresh(&mut display) {
        Ok(()) => 0,
        Err(error) => error.to_errno(),
    };
    if result != 0 && result != data.last_error {
        pr_warn!(
            "redraw failed ({}); retry on next hotplug or DRM lastclose\n",
            result
        );
    }
    data.last_error = result;
    result
}

unsafe extern "C" fn hotplug(client: *mut bindings::drm_client_dev) -> i32 {
    // SAFETY: Callback contract described at refresh(). A nonzero hotplug return
    // permanently disables later callbacks in Linux 6.12, so errors are logged
    // but must never escape this callback.
    unsafe {
        refresh(client);
    }
    0
}

unsafe extern "C" fn restore(client: *mut bindings::drm_client_dev) -> i32 {
    // SAFETY: DRM lastclose holds clientlist_mutex, as does hotplug.
    unsafe { refresh(client) }
}

unsafe extern "C" fn unregister(client: *mut bindings::drm_client_dev) {
    // SAFETY: DRM removed this client from its list and holds clientlist_mutex;
    // no future callbacks can access it. Reclaim the allocation exactly once.
    let state = unsafe { KBox::from_raw(client.cast::<Client>()) };
    // SAFETY: The C client remains alive until release below.
    let mut display = unsafe { drm::Display::new(client) };
    lifecycle::Display::configure(&mut display, &lifecycle::empty());
    // SAFETY: Callback serialization gives exclusive access to this data. Drop
    // all buffers before closing the internal DRM file that owns them.
    let data = unsafe { &mut *state.data.get() };
    data.screen = lifecycle::Screen::new();
    // SAFETY: All buffer and modeset references are detached; this is the sole
    // release of a successfully initialized client, inside unregister.
    unsafe {
        bindings::drm_client_release(client);
    }
    drop(state);
}
