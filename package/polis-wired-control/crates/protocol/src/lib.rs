#![no_std]
#![forbid(unsafe_code)]

pub mod control;
pub mod timing;

use heapless::Vec;
use polis_wired_framing::{Error as FrameError, Frame, Header, encode_message, wire_capacity};
use serde::{Deserialize, Serialize};

pub const PROTOCOL_ID: u16 = 0x5057;
pub const SCHEMA_VERSION: u16 = 3;
pub const MAX_PAYLOAD: u16 = 256;
pub const WIRE_CAPACITY: usize = wire_capacity(MAX_PAYLOAD);
pub const UART_CHUNK: usize = 128;
pub const DEFAULT_BAUD: u32 = 115_200;
pub const MIN_BAUD: u32 = 9_600;
pub const MAX_BAUD: u32 = 921_600;
pub const MIN_OFF_MS: u32 = 100;
pub const MAX_OFF_MS: u32 = 60_000;
pub type Bytes = Vec<u8, UART_CHUNK>;

// Header IDs are independent of the Postcard enum discriminants below. Enum
// order and field layout are frozen within schema 3; change the schema to edit.
pub mod message_type {
    pub const REQUEST: u16 = 1;
    pub const RESPONSE: u16 = 2;
    pub const UART_RX: u16 = 3;
    pub const HEARTBEAT: u16 = 4;
}

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

#[derive(Debug)]
pub enum DecodeError {
    WrongProtocol,
    UnsupportedSchema,
    UnknownMessage,
    Oversized,
    Frame(FrameError),
}

impl From<FrameError> for DecodeError {
    fn from(error: FrameError) -> Self {
        Self::Frame(error)
    }
}

impl Message {
    pub fn encode<'a>(
        &self,
        scratch: &mut [u8; MAX_PAYLOAD as usize],
        wire: &'a mut [u8; WIRE_CAPACITY],
    ) -> Result<&'a [u8], FrameError> {
        let header = |message_type| Header {
            protocol_id: PROTOCOL_ID,
            schema_version: SCHEMA_VERSION,
            message_type,
        };
        match self {
            Self::Request(value) => {
                encode_message(header(message_type::REQUEST), value, scratch, wire)
            }
            Self::Response(value) => {
                encode_message(header(message_type::RESPONSE), value, scratch, wire)
            }
            Self::UartRx(value) => {
                encode_message(header(message_type::UART_RX), value, scratch, wire)
            }
            Self::Heartbeat(value) => {
                encode_message(header(message_type::HEARTBEAT), value, scratch, wire)
            }
        }
    }

    pub fn decode(frame: Frame<'_>) -> Result<Self, DecodeError> {
        if frame.header.protocol_id != PROTOCOL_ID {
            return Err(DecodeError::WrongProtocol);
        }
        if frame.header.schema_version != SCHEMA_VERSION {
            return Err(DecodeError::UnsupportedSchema);
        }
        if frame.payload.len() > MAX_PAYLOAD as usize {
            return Err(DecodeError::Oversized);
        }
        Ok(match frame.header.message_type {
            message_type::REQUEST => Self::Request(frame.deserialize()?),
            message_type::RESPONSE => Self::Response(frame.deserialize()?),
            message_type::UART_RX => Self::UartRx(frame.deserialize()?),
            message_type::HEARTBEAT => Self::Heartbeat(frame.deserialize()?),
            _ => return Err(DecodeError::UnknownMessage),
        })
    }
}
