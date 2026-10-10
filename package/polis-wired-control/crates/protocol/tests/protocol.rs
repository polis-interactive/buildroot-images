use polis_wired_framing::{Decoder, Frame, Header};
use polis_wired_protocol::{
    control::{Power, Session},
    *,
};

fn key(id: u32) -> Key {
    Key { session: 123, id }
}
fn status() -> Status {
    Status {
        timing: timing::BootTiming {
            key: Some(Key {
                session: u64::MAX,
                id: u32::MAX,
            }),
            enable_before_us: u64::MAX,
            enable_after_us: u64::MAX,
            entry_observed_us: Some(u64::MAX),
            entry_count: u32::MAX,
        },
        boot_mode: BootMode::Recovery,
        uptime_us: u64::MAX,
        power_on: true,
        cycle: Some(Cycle {
            boot_mode: BootMode::Recovery,
            key: Key {
                session: u64::MAX,
                id: u32::MAX,
            },
            power_on_at_us: u64::MAX,
        }),
        uart_baud: u32::MAX,
        uart_tx_busy: true,
        uart_rx_offset: u64::MAX,
        counters: Counters {
            framing_errors: u32::MAX,
            protocol_errors: u32::MAX,
            request_queue_full: u32::MAX,
            response_drops: u32::MAX,
            uart_errors: u32::MAX,
            uart_overruns: u32::MAX,
            uart_rx_dropped_bytes: u64::MAX,
            uart_rx_lost_bytes: u64::MAX,
            usb_tx_failures: u32::MAX,
        },
    }
}

#[test]
fn all_messages_fit_the_budget_and_survive_every_stream_split() {
    let bytes = Bytes::from_slice(&[0xff; UART_CHUNK]).unwrap();
    let key = Key {
        session: u64::MAX,
        id: u32::MAX,
    };
    let mut messages: Vec<Message> = [
        Command::Hello,
        Command::SetRecovery { enabled: true },
        Command::Boot {
            mode: BootMode::Recovery,
            off_ms: MAX_OFF_MS,
        },
        Command::Boot {
            mode: BootMode::Normal,
            off_ms: MIN_OFF_MS,
        },
        Command::Status,
        Command::SetPower { on: true },
        Command::CyclePower { off_ms: MAX_OFF_MS },
        Command::ConfigureUart { baud: MAX_BAUD },
        Command::WriteUart {
            bytes: bytes.clone(),
        },
    ]
    .into_iter()
    .map(|command| Message::Request(Request { key, command }))
    .collect();
    for reply in [
        Reply::Hello {
            device_id: [255; 8],
            boot_id: u64::MAX,
            firmware: [u16::MAX; 3],
            max_uart_chunk: UART_CHUNK as u16,
            status: status(),
        },
        Reply::Status(status()),
        Reply::Accepted,
        Reply::Completed,
    ] {
        messages.push(Message::Response(Response { key, reply }));
    }
    for error in [
        ErrorCode::InvalidRequest,
        ErrorCode::NeedHello,
        ErrorCode::WrongSession,
        ErrorCode::DuplicateRequest,
        ErrorCode::Busy,
        ErrorCode::PowerOff,
        ErrorCode::UartError,
        ErrorCode::Aborted,
    ] {
        messages.push(Message::Response(Response {
            key,
            reply: Reply::Error(error),
        }));
    }
    messages.push(Message::UartRx(UartRx {
        session: u64::MAX,
        timestamp_us: u64::MAX,
        offset: u64::MAX,
        bytes,
    }));
    messages.push(Message::Heartbeat(Heartbeat {
        session: u64::MAX,
        boot_id: u64::MAX,
        status: status(),
    }));
    for message in messages {
        let mut scratch = [0; MAX_PAYLOAD as usize];
        let mut wire = [0; WIRE_CAPACITY];
        let encoded = message.encode(&mut scratch, &mut wire).unwrap();
        for split in 0..=encoded.len() {
            let mut buffer = [0; WIRE_CAPACITY];
            let mut decoder = Decoder::new(&mut buffer);
            let mut actual = Vec::new();
            for chunk in [&encoded[..split], &encoded[split..]] {
                decoder.feed(chunk, |frame| {
                    actual.push(Message::decode(frame.unwrap()).unwrap())
                });
            }
            assert_eq!(actual.as_slice(), core::slice::from_ref(&message));
        }
    }
}

#[test]
fn schema_three_request_payloads_have_fixed_fixtures() {
    let mut buffer = [0; 32];
    let hello = Request {
        key: Key { session: 1, id: 1 },
        command: Command::Hello,
    };
    assert_eq!(postcard::to_slice(&hello, &mut buffer).unwrap(), &[1, 1, 0]);
    let cycle = Request {
        key: Key { session: 2, id: 42 },
        command: Command::CyclePower { off_ms: 1000 },
    };
    assert_eq!(
        postcard::to_slice(&cycle, &mut buffer).unwrap(),
        &[2, 42, 3, 0xe8, 7]
    );
}

#[test]
fn incompatible_headers_and_trailing_payloads_are_rejected() {
    let header = Header {
        protocol_id: PROTOCOL_ID,
        schema_version: SCHEMA_VERSION,
        message_type: message_type::REQUEST,
    };
    let decode = |header, payload| Message::decode(Frame { header, payload });
    assert!(matches!(
        decode(
            Header {
                protocol_id: 0,
                ..header
            },
            &[]
        ),
        Err(DecodeError::WrongProtocol)
    ));
    assert!(matches!(
        decode(
            Header {
                schema_version: 1,
                ..header
            },
            &[]
        ),
        Err(DecodeError::UnsupportedSchema)
    ));
    assert!(matches!(
        decode(
            Header {
                message_type: 99,
                ..header
            },
            &[]
        ),
        Err(DecodeError::UnknownMessage)
    ));
    assert!(decode(header, &[1, 1, 0, 0]).is_err());
    assert!(matches!(
        decode(header, &[0; MAX_PAYLOAD as usize + 1]),
        Err(DecodeError::Oversized)
    ));
}

#[test]
fn validates_durations_baud_and_nonempty_bounded_writes() {
    let valid = |command| {
        Request {
            key: key(1),
            command,
        }
        .validate()
        .is_ok()
    };
    assert!(!valid(Command::CyclePower {
        off_ms: MIN_OFF_MS - 1
    }));
    assert!(valid(Command::CyclePower { off_ms: MIN_OFF_MS }));
    assert!(valid(Command::CyclePower { off_ms: MAX_OFF_MS }));
    assert!(!valid(Command::CyclePower {
        off_ms: MAX_OFF_MS + 1
    }));
    assert!(!valid(Command::ConfigureUart { baud: 0 }));
    assert!(!valid(Command::ConfigureUart { baud: MAX_BAUD + 1 }));
    assert!(valid(Command::ConfigureUart { baud: DEFAULT_BAUD }));
    assert!(!valid(Command::WriteUart {
        bytes: Bytes::new()
    }));
    assert!(valid(Command::WriteUart {
        bytes: Bytes::from_slice(&[0; UART_CHUNK]).unwrap()
    }));
    // Serde must reject an oversized incoming vector before it reaches firmware.
    let mut malicious = vec![1, 1, 5, 0x81, 1]; // key, WriteUart, length 129
    malicious.extend([0; UART_CHUNK + 1]);
    assert!(postcard::from_bytes::<Request>(&malicious).is_err());
}

#[test]
fn sessions_require_hello_and_never_reexecute_duplicate_ids() {
    let mut session = Session::default();
    assert_eq!(session.accept(key(1)), Err(ErrorCode::NeedHello));
    session.hello(key(1)).unwrap();
    session.accept(key(2)).unwrap();
    assert_eq!(session.accept(key(2)), Err(ErrorCode::DuplicateRequest));
    assert_eq!(session.hello(key(1)), Err(ErrorCode::DuplicateRequest));
    assert_eq!(
        session.accept(Key {
            session: 456,
            id: 3
        }),
        Err(ErrorCode::WrongSession)
    );
    session
        .hello(Key {
            session: 456,
            id: 1,
        })
        .unwrap();
    assert_eq!(session.accept(key(3)), Err(ErrorCode::WrongSession));
    session
        .accept(Key {
            session: 456,
            id: u32::MAX,
        })
        .unwrap();
    assert_eq!(
        session.accept(Key {
            session: 456,
            id: 0
        }),
        Err(ErrorCode::DuplicateRequest)
    );
}

#[test]
fn zero_handshake_identity_is_rejected() {
    for key in [Key { session: 0, id: 1 }, Key { session: 1, id: 0 }] {
        assert_eq!(
            Session::default().hello(key),
            Err(ErrorCode::InvalidRequest)
        );
        assert_eq!(
            Request {
                key,
                command: Command::Hello
            }
            .validate(),
            Err(ErrorCode::InvalidRequest)
        );
    }
}

#[test]
fn cycle_stays_off_until_deadline_and_finishes_once_even_after_new_hello() {
    let mut power = Power::default();
    assert!(!power.is_on());
    power.set(true).unwrap();
    power.start_cycle(key(2), 500, 1000).unwrap();
    assert!(!power.is_on());
    assert_eq!(power.set(true), Err(ErrorCode::Busy));
    assert_eq!(power.set(false), Err(ErrorCode::Busy));
    assert_eq!(power.start_cycle(key(3), 600, 100), Err(ErrorCode::Busy));
    assert_eq!(power.advance(1_000_499), None);
    let mut session = Session::default();
    session
        .hello(Key {
            session: 456,
            id: 1,
        })
        .unwrap();
    assert_eq!(power.cycle().unwrap().key, key(2));
    assert_eq!(power.advance(1_000_500), Some(key(2)));
    assert!(power.is_on());
    assert_eq!(power.advance(2_000_000), None);
    power.set(false).unwrap();
    assert!(!power.is_on());
}

#[test]
fn invalid_cycle_does_not_mutate_power() {
    let mut power = Power::default();
    power.set(true).unwrap();
    assert_eq!(
        power.start_cycle(key(1), 0, 0),
        Err(ErrorCode::InvalidRequest)
    );
    assert_eq!(
        power.start_cycle(key(1), u64::MAX, 100),
        Err(ErrorCode::InvalidRequest)
    );
    assert!(power.is_on());
    assert_eq!(power.cycle(), None);
}

#[test]
fn recovery_selection_and_coordinated_boot_reject_conflicts() {
    let mut power = Power::default();
    assert_eq!(power.mode(), BootMode::Normal);
    power.select_recovery(true).unwrap();
    power
        .start_boot(key(2), 1000, 1000, BootMode::Recovery)
        .unwrap();
    assert_eq!(power.cycle().unwrap().boot_mode, BootMode::Recovery);
    assert_eq!(power.select_recovery(false), Err(ErrorCode::Busy));
    assert_eq!(
        power.start_boot(key(3), 2000, 1000, BootMode::Normal),
        Err(ErrorCode::Busy)
    );
    assert_eq!(power.mode(), BootMode::Recovery);
    assert_eq!(power.advance(1_001_000), Some(key(2)));
    assert_eq!(power.select_recovery(false), Err(ErrorCode::Busy));
    power
        .start_boot(key(4), 2_000_000, 1000, BootMode::Normal)
        .unwrap();
    assert!(!power.is_on());
    assert_eq!(power.mode(), BootMode::Normal);
    assert_eq!(power.advance(3_000_000), Some(key(4)));
}

#[test]
fn invalid_recovery_boot_does_not_change_power_or_selection() {
    let mut power = Power::default();
    power.set(true).unwrap();
    assert_eq!(
        power.start_boot(key(1), 0, 0, BootMode::Recovery),
        Err(ErrorCode::InvalidRequest)
    );
    assert!(power.is_on());
    assert_eq!(power.mode(), BootMode::Normal);
}
