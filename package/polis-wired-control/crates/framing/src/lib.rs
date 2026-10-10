#![no_std]
#![forbid(unsafe_code)]
#![doc = include_str!("../README.md")]

use core::fmt;
use crc::{CRC_32_ISCSI, Crc};
use serde::{Deserialize, Serialize};

/// Version of the fixed header and framing algorithm, independent of payload schemas.
pub const FORMAT_VERSION: u8 = 1;
/// Version byte followed by four little-endian u16 fields.
pub const HEADER_LEN: usize = 9;
pub const CRC_LEN: usize = 4;
/// Suggested payload budget. Applications may select another budget up to u16::MAX.
pub const DEFAULT_MAX_PAYLOAD_LEN: u16 = 1024;

const CRC: Crc<u32> = Crc::<u32>::new(&CRC_32_ISCSI);

/// Upper bound for an encoded frame, including both zero delimiters.
///
/// Use this for transmit and decoder buffers. The u16 argument reflects the
/// wire payload-length limit; there is no mandatory 1024-byte library limit.
pub const fn wire_capacity(max_payload_len: u16) -> usize {
    let raw_len = HEADER_LEN + max_payload_len as usize + CRC_LEN;
    // Include a final COBS code even at exact multiples of 254.
    raw_len + raw_len / 254 + 1 + 2
}

/// Domain-owned identifiers. No registry or domain enum is required by framing.
///
/// Serde support is useful for storing/inspecting headers. The framing encoder
/// explicitly writes these fields as little-endian u16s, not Postcard varints.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct Header {
    pub protocol_id: u16,
    pub schema_version: u16,
    pub message_type: u16,
}

/// A validated frame borrowing its opaque payload from the decoder buffer.
///
/// The borrow lasts until the next decoder operation. Copy or deserialize the
/// payload before feeding more bytes. Serde encoding of this view is distinct
/// from the canonical wire encoding produced by [`encode`].
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct Frame<'a> {
    pub header: Header,
    #[serde(borrow)]
    pub payload: &'a [u8],
}

impl<'a> Frame<'a> {
    /// Deserialize a Postcard payload, rejecting any unconsumed suffix.
    ///
    /// The caller checks protocol/schema/message IDs before choosing `T`.
    pub fn deserialize<T: Deserialize<'a>>(&self) -> Result<T, Error> {
        let (message, rest) = postcard::take_from_bytes(self.payload).map_err(Error::Postcard)?;
        if !rest.is_empty() {
            return Err(Error::TrailingPayload);
        }
        Ok(message)
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Error {
    /// Payload length cannot be represented by the wire u16 field.
    PayloadTooLarge,
    /// Output is smaller than the conservative [`wire_capacity`] bound.
    OutputTooSmall,
    /// Encoded input exceeded the decoder's caller-provided buffer.
    FrameTooLarge,
    InvalidCobs,
    InvalidLength,
    ChecksumMismatch,
    UnsupportedVersion(u8),
    Postcard(postcard::Error),
    TrailingPayload,
    /// Complete encoded frame exceeds the transport's UDP payload budget.
    DatagramTooLarge,
    /// A datagram must contain exactly one complete delimited frame.
    InvalidDatagram,
    /// An explicit end of stream arrived during a frame (not a UDP boundary).
    Truncated,
}

impl fmt::Display for Error {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::PayloadTooLarge => f.write_str("payload exceeds u16 wire length"),
            Self::OutputTooSmall => f.write_str("output buffer is too small"),
            Self::FrameTooLarge => f.write_str("frame exceeds decoder capacity"),
            Self::InvalidCobs => f.write_str("invalid COBS frame"),
            Self::InvalidLength => f.write_str("invalid frame length"),
            Self::ChecksumMismatch => f.write_str("frame checksum mismatch"),
            Self::UnsupportedVersion(version) => write!(f, "unsupported framing version {version}"),
            Self::Postcard(error) => write!(f, "payload serialization: {error}"),
            Self::TrailingPayload => f.write_str("trailing bytes after payload"),
            Self::DatagramTooLarge => f.write_str("frame exceeds UDP datagram budget"),
            Self::InvalidDatagram => {
                f.write_str("datagram must contain exactly one complete frame")
            }
            Self::Truncated => f.write_str("stream ended during a frame"),
        }
    }
}

impl core::error::Error for Error {}

/// Encode opaque bytes as `0 | COBS(header + payload + CRC-32C) | 0`.
///
/// Reserve [`wire_capacity(payload.len() as u16)`](wire_capacity) bytes after
/// checking the payload fits u16. On error, do not transmit any output bytes.
pub fn encode<'a>(header: Header, payload: &[u8], output: &'a mut [u8]) -> Result<&'a [u8], Error> {
    let payload_len = u16::try_from(payload.len()).map_err(|_| Error::PayloadTooLarge)?;
    if output.len() < wire_capacity(payload_len) {
        return Err(Error::OutputTooSmall);
    }
    let mut raw_header = [0; HEADER_LEN];
    raw_header[0] = FORMAT_VERSION;
    raw_header[1..3].copy_from_slice(&header.protocol_id.to_le_bytes());
    raw_header[3..5].copy_from_slice(&header.schema_version.to_le_bytes());
    raw_header[5..7].copy_from_slice(&header.message_type.to_le_bytes());
    raw_header[7..9].copy_from_slice(&payload_len.to_le_bytes());

    let mut digest = CRC.digest();
    digest.update(&raw_header);
    digest.update(payload);
    let checksum = digest.finalize().to_le_bytes();

    output[0] = 0;
    let mut encoder = cobs::CobsEncoder::new(&mut output[1..]);
    for part in [raw_header.as_slice(), payload, checksum.as_slice()] {
        encoder.push(part).map_err(|_| Error::OutputTooSmall)?;
    }
    let len = encoder.finalize() + 1;
    output[len] = 0;
    Ok(&output[..len + 1])
}

/// Serialize a typed payload into scratch space and encode the complete frame.
pub fn encode_message<'a, T: Serialize + ?Sized>(
    header: Header,
    message: &T,
    payload_scratch: &mut [u8],
    output: &'a mut [u8],
) -> Result<&'a [u8], Error> {
    let payload = postcard::to_slice(message, payload_scratch).map_err(Error::Postcard)?;
    encode(header, payload, output)
}

/// Encode exactly one frame for one UDP datagram, enforcing the payload budget.
///
/// The budget includes COBS, header, checksum, and both delimiters, but excludes
/// IP/UDP headers. Send the returned slice in one call; never split it. Output
/// still needs the conservative [`wire_capacity`] bound required by [`encode`].
pub fn encode_datagram<'a>(
    header: Header,
    payload: &[u8],
    max_datagram_len: usize,
    output: &'a mut [u8],
) -> Result<&'a [u8], Error> {
    let wire = encode(header, payload, output)?;
    if wire.len() > max_datagram_len {
        return Err(Error::DatagramTooLarge);
    }
    Ok(wire)
}

/// Serialize a typed payload and encode one bounded UDP datagram.
pub fn encode_message_datagram<'a, T: Serialize + ?Sized>(
    header: Header,
    message: &T,
    max_datagram_len: usize,
    payload_scratch: &mut [u8],
    output: &'a mut [u8],
) -> Result<&'a [u8], Error> {
    let payload = postcard::to_slice(message, payload_scratch).map_err(Error::Postcard)?;
    encode_datagram(header, payload, max_datagram_len, output)
}

/// Decode exactly one complete UDP frame in place. No state crosses datagrams.
///
/// Rejects oversized datagrams, missing delimiters, multiple frames, padding,
/// and trailing bytes. The input must be the entire datagram: the transport
/// adapter must detect/reject socket-level truncation before calling this.
pub fn decode_datagram(datagram: &mut [u8], max_datagram_len: usize) -> Result<Frame<'_>, Error> {
    if datagram.len() > max_datagram_len {
        return Err(Error::DatagramTooLarge);
    }
    if datagram.len() < 3 || datagram[0] != 0 || datagram[datagram.len() - 1] != 0 {
        return Err(Error::InvalidDatagram);
    }
    let end = datagram.len() - 1;
    let body = &mut datagram[1..end];
    if body.contains(&0) {
        return Err(Error::InvalidDatagram);
    }
    decode_body(body)
}

/// Incremental decoder for a single ordered RS-485 or TCP byte stream.
///
/// Input may be split anywhere or contain several frames per chunk. Delimiters
/// synchronize the stream; startup garbage is ignored until the first zero.
/// Overflow emits one error, discards through the next zero, then resumes.
pub struct Decoder<'a> {
    buffer: &'a mut [u8],
    len: usize,
    discarding: bool,
}

impl<'a> Decoder<'a> {
    pub fn new(buffer: &'a mut [u8]) -> Self {
        Self {
            buffer,
            len: 0,
            discarding: true,
        }
    }

    /// Forget the current stream. Ignore bytes until the next delimiter.
    /// Use after reconnecting, changing peers, or a caller-owned receive timeout.
    pub fn reset(&mut self) {
        self.len = 0;
        self.discarding = true;
    }

    /// Finish an actual stream, reporting a missing closing delimiter.
    /// UDP uses [`decode_datagram`] instead of this stream decoder.
    pub fn finish(&mut self) -> Result<(), Error> {
        let truncated = self.len != 0;
        self.reset();
        if truncated {
            Err(Error::Truncated)
        } else {
            Ok(())
        }
    }

    /// Consume one byte. A frame or error event does not stop future decoding.
    pub fn push(&mut self, byte: u8) -> Option<Result<Frame<'_>, Error>> {
        if byte == 0 {
            self.discarding = false;
            let len = core::mem::take(&mut self.len);
            if len == 0 {
                return None;
            }
            return Some(decode_body(&mut self.buffer[..len]));
        }
        if self.discarding {
            return None;
        }
        if self.len == self.buffer.len() {
            self.len = 0;
            self.discarding = true;
            return Some(Err(Error::FrameTooLarge));
        }
        self.buffer[self.len] = byte;
        self.len += 1;
        None
    }

    /// Consume every byte in a stream read, emitting all complete frames/errors.
    /// Borrowed frames must be handled within the callback.
    pub fn feed(&mut self, bytes: &[u8], mut on_event: impl FnMut(Result<Frame<'_>, Error>)) {
        for &byte in bytes {
            if let Some(event) = self.push(byte) {
                on_event(event);
            }
        }
    }
}

fn decode_body(buffer: &mut [u8]) -> Result<Frame<'_>, Error> {
    let len = cobs::decode_in_place(buffer).map_err(|_| Error::InvalidCobs)?;
    if len < HEADER_LEN + CRC_LEN {
        return Err(Error::InvalidLength);
    }
    let crc_start = len - CRC_LEN;
    let bytes = &buffer[..crc_start];
    let checksum = u32::from_le_bytes(buffer[crc_start..len].try_into().unwrap());
    if CRC.checksum(bytes) != checksum {
        return Err(Error::ChecksumMismatch);
    }
    if bytes[0] != FORMAT_VERSION {
        return Err(Error::UnsupportedVersion(bytes[0]));
    }
    let field = |offset| u16::from_le_bytes([bytes[offset], bytes[offset + 1]]);
    if usize::from(field(7)) != crc_start - HEADER_LEN {
        return Err(Error::InvalidLength);
    }
    Ok(Frame {
        header: Header {
            protocol_id: field(1),
            schema_version: field(3),
            message_type: field(5),
        },
        payload: &bytes[HEADER_LEN..],
    })
}
