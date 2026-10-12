#![no_std]
#![forbid(unsafe_code)]

pub mod control;
pub mod timing;

use core::num::{NonZeroU32, NonZeroU64};
use heapless::Vec;
use polis_control_link_protocol::{ControlLink, Message as LinkMessage};
use serde::{Deserialize, Serialize};

/// USB product name advertised by firmware and used for host discovery.
pub const USB_PRODUCT: &str = "Polis SBC Controller";

/// Stable product identity for the shared control-link envelope. Compatibility
/// checks come from control-link; there is no firmware-version gate here.
/// Keep this on-wire identifier unchanged across package/product renames.
pub const APPLICATION_ID: [u8; 16] = *b"polis-wired-ctrl";
pub const CONTROL_LINK: ControlLink = ControlLink::new(APPLICATION_ID);
pub const MAX_BODY: usize = 256;
pub const MAX_PAYLOAD: usize = polis_control_link_protocol::PREFIX_LEN + MAX_BODY;
pub const WIRE_CAPACITY: usize = polis_framing::wire_capacity(MAX_PAYLOAD as u16);
pub const UART_CHUNK: usize = 128;
pub const DEFAULT_BAUD: u32 = 115_200;
pub const MIN_BAUD: u32 = 9_600;
pub const MAX_BAUD: u32 = 921_600;
pub const MIN_OFF_MS: u32 = 100;
pub const MAX_OFF_MS: u32 = 60_000;
pub type Bytes = Vec<u8, UART_CHUNK>;

// Postcard variant order and fields form the product body contract.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct Key {
    /// Nonzero random nonce chosen anew by the host on each connection.
    pub session: u64,
    /// Strictly increasing within a session; zero is reserved.
    pub id: u32,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct Request {
    pub key: Key,
    pub command: Command,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub enum Command {
    Hello,
    Status,
    SetPower { on: bool },
    CyclePower { off_ms: u32 },
    ConfigureUart { baud: u32 },
    WriteUart { bytes: Bytes },
    SetRecovery { enabled: bool },
    Boot { mode: BootMode, off_ms: u32 },
}

impl Request {
    pub fn validate(&self) -> Result<(), ErrorCode> {
        if self.key.session == 0 || self.key.id == 0 {
            return Err(ErrorCode::InvalidRequest);
        }
        match &self.command {
            Command::CyclePower { off_ms } | Command::Boot { off_ms, .. }
                if !(MIN_OFF_MS..=MAX_OFF_MS).contains(off_ms) =>
            {
                Err(ErrorCode::InvalidRequest)
            }
            Command::ConfigureUart { baud } if !(MIN_BAUD..=MAX_BAUD).contains(baud) => {
                Err(ErrorCode::InvalidRequest)
            }
            Command::WriteUart { bytes } if bytes.is_empty() => Err(ErrorCode::InvalidRequest),
            _ => Ok(()),
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub enum ErrorCode {
    InvalidRequest,
    NeedHello,
    WrongSession,
    DuplicateRequest,
    Busy,
    PowerOff,
    UartError,
    Aborted,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct Response {
    pub key: Key,
    pub reply: Reply,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub enum Reply {
    Hello {
        device_id: [u8; 8],
        boot_id: u64,
        firmware: [u16; 3],
        max_uart_chunk: u16,
        status: Status,
    },
    Status(Status),
    Accepted,
    Completed,
    Error(ErrorCode),
}

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct Counters {
    pub framing_errors: u32,
    pub protocol_errors: u32,
    pub request_queue_full: u32,
    pub response_drops: u32,
    pub uart_errors: u32,
    pub uart_overruns: u32,
    pub uart_rx_dropped_bytes: u64,
    /// Queue/USB loss, excluding intentional discards while power is off.
    pub uart_rx_lost_bytes: u64,
    pub usb_tx_failures: u32,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct Status {
    pub timing: timing::BootTiming,
    pub boot_mode: BootMode,
    pub uptime_us: u64,
    pub power_on: bool,
    pub cycle: Option<Cycle>,
    pub uart_baud: u32,
    pub uart_tx_busy: bool,
    /// Counts received bytes even when USB output is dropped. Overruns mean
    /// additional bytes may have been lost before software could count them.
    pub uart_rx_offset: u64,
    pub counters: Counters,
}

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
pub enum BootMode {
    #[default]
    Normal,
    Recovery,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct Cycle {
    pub boot_mode: BootMode,
    pub key: Key,
    pub power_on_at_us: u64,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct UartRx {
    pub session: u64,
    /// Pico uptime when this batch was read, not a per-byte arrival time.
    pub timestamp_us: u64,
    pub offset: u64,
    pub bytes: Bytes,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct Heartbeat {
    pub session: u64,
    pub boot_id: u64,
    pub status: Status,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Message {
    Request(Request),
    Response(Response),
    UartRx(UartRx),
    Heartbeat(Heartbeat),
}

/// Event bodies do not repeat the session carried by control-link.
#[derive(Serialize, Deserialize)]
enum Event {
    UartRx {
        timestamp_us: u64,
        offset: u64,
        bytes: Bytes,
    },
    Heartbeat {
        boot_id: u64,
        status: Status,
    },
}

#[derive(Debug)]
pub enum CodecError {
    InvalidKey,
    Oversized,
    Payload(postcard::Error),
    TrailingBytes,
    UnexpectedAccepted,
}

impl core::fmt::Display for CodecError {
    fn fmt(&self, f: &mut core::fmt::Formatter<'_>) -> core::fmt::Result {
        write!(f, "SBC controller application codec: {self:?}")
    }
}
impl core::error::Error for CodecError {}
impl From<postcard::Error> for CodecError {
    fn from(error: postcard::Error) -> Self {
        Self::Payload(error)
    }
}

impl TryFrom<Key> for polis_control_link_protocol::Key {
    type Error = CodecError;
    fn try_from(key: Key) -> Result<Self, Self::Error> {
        Ok(Self {
            session: NonZeroU64::new(key.session).ok_or(CodecError::InvalidKey)?,
            id: NonZeroU32::new(key.id).ok_or(CodecError::InvalidKey)?,
        })
    }
}
impl From<polis_control_link_protocol::Key> for Key {
    fn from(key: polis_control_link_protocol::Key) -> Self {
        Self {
            session: key.session.get(),
            id: key.id.get(),
        }
    }
}

fn decode_body<T: serde::de::DeserializeOwned>(body: &[u8]) -> Result<T, CodecError> {
    if body.len() > MAX_BODY {
        return Err(CodecError::Oversized);
    }
    let (value, rest) = postcard::take_from_bytes(body)?;
    if !rest.is_empty() {
        return Err(CodecError::TrailingBytes);
    }
    Ok(value)
}

impl Message {
    /// Borrowed envelope for either runtime adapter; all storage is caller-owned.
    pub fn to_link<'a>(&self, body: &'a mut [u8; MAX_BODY]) -> Result<LinkMessage<'a>, CodecError> {
        Ok(match self {
            Self::Request(value) => LinkMessage::Request {
                key: value.key.try_into()?,
                body: postcard::to_slice(&value.command, body)?,
            },
            Self::Response(Response {
                key,
                reply: Reply::Accepted,
            }) => LinkMessage::Accepted {
                key: (*key).try_into()?,
            },
            Self::Response(value) => LinkMessage::Response {
                key: value.key.try_into()?,
                body: postcard::to_slice(&value.reply, body)?,
            },
            Self::UartRx(value) => LinkMessage::Event {
                session: NonZeroU64::new(value.session).ok_or(CodecError::InvalidKey)?,
                body: postcard::to_slice(
                    &Event::UartRx {
                        timestamp_us: value.timestamp_us,
                        offset: value.offset,
                        bytes: value.bytes.clone(),
                    },
                    body,
                )?,
            },
            Self::Heartbeat(value) => LinkMessage::Event {
                session: NonZeroU64::new(value.session).ok_or(CodecError::InvalidKey)?,
                body: postcard::to_slice(
                    &Event::Heartbeat {
                        boot_id: value.boot_id,
                        status: value.status,
                    },
                    body,
                )?,
            },
        })
    }

    /// Decode inside the runtime receiver's callback so no borrowed buffer escapes.
    pub fn from_link(message: LinkMessage<'_>) -> Result<Self, CodecError> {
        Ok(match message {
            LinkMessage::Request { key, body } => Self::Request(Request {
                key: key.into(),
                command: decode_body(body)?,
            }),
            LinkMessage::Accepted { key } => Self::Response(Response {
                key: key.into(),
                reply: Reply::Accepted,
            }),
            LinkMessage::Response { key, body } => {
                let reply = decode_body(body)?;
                // Accepted is nonterminal and must use its envelope, not free a request slot.
                if reply == Reply::Accepted {
                    return Err(CodecError::UnexpectedAccepted);
                }
                Self::Response(Response {
                    key: key.into(),
                    reply,
                })
            }
            LinkMessage::Event { session, body } => match decode_body(body)? {
                Event::UartRx {
                    timestamp_us,
                    offset,
                    bytes,
                } => Self::UartRx(UartRx {
                    session: session.get(),
                    timestamp_us,
                    offset,
                    bytes,
                }),
                Event::Heartbeat { boot_id, status } => Self::Heartbeat(Heartbeat {
                    session: session.get(),
                    boot_id,
                    status,
                }),
            },
        })
    }
}
