# Portable framing

Copied from Slate's `slate-framing`; see [PROVENANCE.md](PROVENANCE.md).

`polis-wired-framing` is an allocation-free `no_std` byte codec for desktop, RP2040,
and Arm RP235x. It has no I/O, HAL, executor, clock, or domain dependency.
Serde/Postcard helpers support typed payloads; raw APIs accept any payload bytes.

```rust
use serde::{Deserialize, Serialize};
use polis_wired_framing::{Decoder, Header, encode_message, wire_capacity};

#[derive(Debug, PartialEq, Serialize, Deserialize)]
struct Ping { sequence: u32 }

let header = Header { protocol_id: 1, schema_version: 1, message_type: 1 };
let mut scratch = [0; 32];
let mut tx = [0; wire_capacity(32)];
let wire = encode_message(header, &Ping { sequence: 42 }, &mut scratch, &mut tx)?;
let mut rx = [0; wire_capacity(32)];
let mut decoder = Decoder::new(&mut rx);
for read in wire.chunks(3) {
    decoder.feed(read, |event| {
        let frame = event.unwrap();
        assert_eq!(frame.header, header);
        assert_eq!(frame.deserialize::<Ping>().unwrap(), Ping { sequence: 42 });
    });
}
# Ok::<(), polis_wired_framing::Error>(())
```

## Wire contract, version 1

Every frame is `00 | COBS(header + payload + CRC-32C) | 00`. The leading zero
terminates any abandoned prefix. Empty delimiter runs are ignored. Header fields
are fixed-width and little-endian, independently of Serde's representation:

| Decoded offset | Width | Meaning |
| --- | --- | --- |
| 0 | 1 | Framing format version, currently 1 |
| 1 | 2 | Protocol identifier, assigned by the consumer |
| 3 | 2 | Protocol schema version, assigned by the consumer |
| 5 | 2 | Message type, assigned by the consumer |
| 7 | 2 | Payload byte length, excluding header and checksum |
| 9 | variable | Opaque payload, Postcard when using typed helpers |
| 9 + payload length | 4 | CRC-32C, little-endian |

CRC-32C uses CRC-32/ISCSI (Castagnoli): polynomial `0x1edc6f41`, initial value
and xor-out `0xffffffff`, reflected input/output; `123456789` checks to
`0xe3069283`. Coverage is the entire decoded header and payload. COBS encoding
and zero delimiters are outside the checksum. CRC detects accidental corruption;
it is not authentication.

The wire supports payloads up to 65,535 bytes. The suggested payload budget is
1,024 bytes, not a mandatory frame limit. Callers supply all memory. Reserve
`wire_capacity(payload_budget)` for TX and RX and a separate payload scratch
buffer for typed serialization. Encoding requires that conservative bound even
when a particular frame would fit a smaller buffer. The decoder's buffer bounds
the encoded body; oversized frames are discarded through the next delimiter.

Validation checks COBS, minimum size, CRC, framing version, and exact payload
length before returning a borrowed frame. Unknown protocol/schema/message IDs
remain available to the caller for rejection, dispatch, or forwarding. Check
these IDs before choosing a deserialization type. Typed decoding rejects trailing
payload bytes. A valid CRC does not establish a valid domain message.

`Header` and `Frame` also implement Serde for inspection/storage, but serializing
those views directly does not produce this wire format. Use `encode` or
`encode_message`. Domain enums must explicitly map stable IDs to the header;
Rust layout attributes and ordinary Serde enum derives do not establish those
IDs. Never reuse IDs for incompatible message meanings. Schema negotiation and
the compatibility of nested Postcard enums/fields remain protocol responsibilities.

## Transport integration

RS-485 and TCP reads feed the same persistent decoder; short reads, arbitrary
splits, and multiple frames in one read are supported. Encode a complete frame
before handing its slice to the RS-485 driver's `send`, preserving DE ownership
through the complete transmission. After cancelled TX, recover the driver before
transmitting again. The next frame's leading zero permits parser recovery.

For UDP, **exactly one complete frame must fit in one datagram**. Use
`encode_datagram` or `encode_message_datagram` with a transport-selected maximum
UDP payload size. The budget includes the encoded header, payload, checksum,
COBS overhead, and delimiters; it excludes IP/UDP headers. Oversized frames
return `DatagramTooLarge`; do not split or transmit them. Choose the budget for
the network path so IP fragmentation is unnecessary. Fragmentation/reassembly
is deliberately deferred until a real requirement calls for it.

Use `decode_datagram` on each complete received datagram. It rejects missing
boundaries, multiple frames, padding, trailing bytes, excessive size, and
corruption. It holds no state between datagrams. The socket adapter must detect
and reject receive truncation (or provide a buffer large enough for the entire
datagram); a silently truncated datagram is not a valid input contract. Inspect
protocol/schema/message IDs before deserializing.

Framing does not detect whole-frame loss, duplication, or reordering. Sequence
IDs, deadlines, retries, peer identity, RS-485 addresses, acknowledgments,
arbitration, and timing remain consumer responsibilities.

`Decoder::finish` is for actual TCP EOF/end-of-stream and reports truncation.
`reset` starts a new stream and waits for a zero. Startup garbage is ignored
until a zero, and each overflow emits one error. Stream frames borrow the
receive buffer until the next decoder operation; process or copy them within
`feed`'s callback. UDP frames borrow the datagram buffer decoded in place.

## Validation

From `package/polis-wired-control/`:

```sh
cargo test -p polis-wired-framing
cargo clippy -p polis-wired-framing --all-targets -- -D warnings
cargo build -p polis-wired-framing --target thumbv8m.main-none-eabihf
```

The original codec and its stream/datagram tests are retained together; this
fixture uses only stream framing. Host tests and builds do not qualify physical
UART reception, electrical timing, or USB delivery on hardware.
