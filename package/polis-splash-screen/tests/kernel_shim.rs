// SPDX-License-Identifier: GPL-2.0-only
//! HOST TYPE-CHECK ONLY. Never copied into or linked with the target kernel.
//! Uses real bindgen output for every C layout/signature. Rust allocation and
//! logging facades only approximate the pinned kernel API: this is not a kernel
//! compilation/link check and deliberately cannot validate hardware behavior.
#![allow(
    non_camel_case_types,
    non_snake_case,
    non_upper_case_globals,
    dead_code
)]

pub mod bindings {
    #![allow(improper_ctypes, unnecessary_transmutes)]
    type __kernel_size_t = usize;
    type __kernel_ssize_t = isize;
    type __kernel_ptrdiff_t = isize;
    include!(env!("POLIS_TEST_BINDINGS"));
}

pub mod error {
    #[derive(Debug)]
    pub struct Error(i32);
    pub type Result<T = ()> = core::result::Result<T, Error>;
    impl Error {
        pub fn from_errno(value: i32) -> Self {
            Self(value)
        }
        pub fn to_errno(self) -> i32 {
            self.0
        }
    }
    pub fn from_err_ptr<T>(value: *mut T) -> Result<*mut T> {
        if (value as usize) >= usize::MAX - 4094 {
            Err(Error(value as isize as i32))
        } else {
            Ok(value)
        }
    }
    pub mod code {
        use super::Error;
        pub const EINVAL: Error = Error(-(crate::bindings::EINVAL as i32));
        pub const ENOMEM: Error = Error(-(crate::bindings::ENOMEM as i32));
        pub const EFAULT: Error = Error(-(crate::bindings::EFAULT as i32));
        pub const E2BIG: Error = Error(-(crate::bindings::E2BIG as i32));
    }
}

pub mod types {
    use core::{cell::UnsafeCell, marker::PhantomPinned, mem::MaybeUninit};
    #[repr(transparent)]
    pub struct Opaque<T> {
        value: UnsafeCell<MaybeUninit<T>>,
        _pin: PhantomPinned,
    }
    impl<T> Opaque<T> {
        pub const fn uninit() -> Self {
            Self {
                value: UnsafeCell::new(MaybeUninit::uninit()),
                _pin: PhantomPinned,
            }
        }
    }
}

pub struct KBox<T>(Box<T>);
impl<T> KBox<T> {
    pub fn new(value: T, _: u32) -> Result<Self, ()> {
        Ok(Self(Box::new(value)))
    }
    pub fn into_raw(value: Self) -> *mut T {
        Box::into_raw(value.0)
    }
    pub unsafe fn from_raw(value: *mut T) -> Self {
        Self(unsafe { Box::from_raw(value) })
    }
}
impl<T> core::ops::Deref for KBox<T> {
    type Target = T;
    fn deref(&self) -> &T {
        &self.0
    }
}
pub struct CStr(&'static [u8]);
impl CStr {
    pub const fn from_bytes(bytes: &'static [u8]) -> Self {
        Self(bytes)
    }
    pub const fn as_char_ptr(&self) -> *const core::ffi::c_char {
        self.0.as_ptr().cast()
    }
}
#[macro_export]
macro_rules! c_str {
    ($value:literal) => {
        $crate::CStr::from_bytes(concat!($value, "\0").as_bytes())
    };
}
#[macro_export]
macro_rules! pr_info { ($($args:tt)*) => { let _ = (crate::__LOG_PREFIX, core::format_args!($($args)*)); }; }
#[macro_export]
macro_rules! pr_warn { ($($args:tt)*) => { let _ = (crate::__LOG_PREFIX, core::format_args!($($args)*)); }; }
pub mod prelude {
    pub use crate::{pr_info, pr_warn, KBox};
    pub const GFP_KERNEL: u32 = 0;
}
