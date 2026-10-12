//! Cold-boot measurements stay entirely in the Pico clock domain.
use crate::workflow::{Backend, Journal, RealBackend, validate_capture};
use anyhow::{Context, Result, ensure};
use polis_sbc_controller_embedded_api::{BootMode, Command, Reply, Status};
use serde_json::{Value, json};

fn measurement(before: Status, enabled: Status, final_status: Status) -> Result<Value> {
    let edge = enabled.timing;
    let final_edge = final_status.timing;
    ensure!(
        !before.power_on && before.cycle.is_none(),
        "missing powered-off checkpoint"
    );
    ensure!(
        enabled.power_on
            && final_status.power_on
            && enabled.boot_mode == BootMode::Normal
            && final_status.boot_mode == BootMode::Normal
            && final_status.cycle.is_none(),
        "not a completed normal boot"
    );
    ensure!(
        edge.key.is_some() && edge.key != before.timing.key && edge.key == final_edge.key,
        "power operation changed or stale enable event"
    );
    ensure!(
        edge.enable_before_us >= before.uptime_us
            && edge.enable_after_us >= edge.enable_before_us
            && edge.enable_after_us <= enabled.uptime_us
            && edge.enable_before_us == final_edge.enable_before_us
            && edge.enable_after_us == final_edge.enable_after_us,
        "invalid power-enable timestamps"
    );
    ensure!(
        final_edge.entry_count == 1,
        "expected exactly one PLIS_ENTRY_V1 marker; rebuild Pi kernel or inspect UART capture"
    );
    let observed = final_edge
        .entry_observed_us
        .context("kernel-entry marker missing")?;
    ensure!(
        observed >= edge.enable_after_us && observed <= final_status.uptime_us,
        "invalid entry timestamp"
    );
    ensure!(
        enabled.uart_baud == 115200 && final_status.uart_baud == 115200,
        "benchmark requires UART 115200"
    );
    validate_capture(before.counters, final_status.counters)?;
    Ok(json!({
        "milestone":"arm64 primary_entry direct PL011 marker, observed by Pico UART task",
        "clock":"Pico monotonic microseconds",
        "power_request":edge.key,
        "enable_before_us":edge.enable_before_us,"enable_after_us":edge.enable_after_us,
        "entry_observed_us":observed,
        "enable_to_marker_observed_us":observed-edge.enable_after_us,
        "enable_bracket_width_us":edge.enable_after_us-edge.enable_before_us,
        "limits":"Not an exact CPU-entry or 5 V rail measurement. Includes serial transmission and Pico RX/task latency; no latency subtraction or cross-clock addition.",
        "marker_wire_time_us":(polis_sbc_controller_embedded_api::timing::ENTRY_MARKER.len() as f64)*10.0*1_000_000.0/115200.0
    }))
}

pub async fn execute(parent: &RealBackend, count: u32, journal: &Journal) -> Result<()> {
    ensure!((1..=20).contains(&count), "samples must be 1..20");
    let mut samples = Vec::new();
    let mut boot_ids = std::collections::HashSet::new();
    for index in 1..=count {
        journal
            .stage(&format!("benchmark_{index}_of_{count}"))
            .await?;
        let dir = parent.run_dir.join(format!("sample-{index:02}"));
        tokio::fs::create_dir_all(&dir).await?;
        let backend = RealBackend::new(
            parent.config.clone(),
            parent.link.clone(),
            parent.generation,
            dir.clone(),
        );
        let start = parent.link.cursor();
        backend.normal_boot().await?;
        let ready = backend.ready().await;
        // Retain raw output for a failed sample as well as a successful one.
        parent.link.sync_logs()?;
        let end = parent.link.cursor();
        {
            use std::io::{Read, Seek, SeekFrom, Write};
            let mut raw = std::fs::File::open(parent.config.paths.state_dir.join("uart.bin"))?;
            raw.seek(SeekFrom::Start(start))?;
            let mut destination = std::fs::File::create(dir.join("uart.log"))?;
            std::io::copy(&mut raw.take(end - start), &mut destination)?;
            destination.flush()?;
            destination.sync_all()?;
        }
        ensure!(
            ready?,
            "benchmark boot missed readiness; no automatic recovery during measurement"
        );
        let capture: Value = serde_json::from_slice(
            &tokio::fs::read(dir.join("capture.json"))
                .await
                .context("benchmark requires USB diagnostics; see sample capture-error.txt")?,
        )?;
        let boot_id = capture["boot_id"]
            .as_str()
            .context("missing diagnostic boot ID")?;
        ensure!(
            boot_ids.insert(boot_id.to_owned()),
            "repeated Linux boot ID; Pi did not cold boot"
        );
        let power: Value =
            serde_json::from_slice(&tokio::fs::read(dir.join("power-enable.json")).await?)?;
        let Reply::Status(status) = backend.pico(Command::Status).await? else {
            anyhow::bail!("expected status");
        };
        let result = measurement(
            serde_json::from_value(power["before"].clone())?,
            serde_json::from_value(power["enabled"].clone())?,
            status,
        )?;
        let sample = json!({"index":index,"linux_boot_id":boot_id,"pico_boot_id":power["pico_boot_id"],
            "generation":parent.generation,"uart_start":start,"uart_end":end,
            "timing":result,"diagnostics":capture,"final_status":status});
        tokio::fs::write(
            dir.join("measurement.json"),
            serde_json::to_vec_pretty(&sample)?,
        )
        .await?;
        samples.push(sample);
        let mut durations: Vec<u64> = samples
            .iter()
            .map(|s| {
                s["timing"]["enable_to_marker_observed_us"]
                    .as_u64()
                    .unwrap()
            })
            .collect();
        durations.sort_unstable();
        let middle = durations.len() / 2;
        let median = if durations.len().is_multiple_of(2) {
            (durations[middle - 1] as f64 + durations[middle] as f64) / 2.0
        } else {
            durations[middle] as f64
        };
        let summary = json!({"complete":index==count,"requested_samples":count,"completed_samples":index,
            "metric":"enable_to_marker_observed_us","min_us":durations[0],"median_us":median,
            "max_us":durations.last(),"samples":samples});
        let temp = parent.run_dir.join("benchmark.tmp");
        tokio::fs::write(&temp, serde_json::to_vec_pretty(&summary)?).await?;
        tokio::fs::rename(temp, parent.run_dir.join("benchmark.json")).await?;
        crate::logging::event(format!(
            "[benchmark {index}/{count}] enable to kernel-entry marker: {} us; diagnostics archived",
            result["enable_to_marker_observed_us"]
        ));
    }
    journal.stage("benchmark_complete").await
}

#[cfg(test)]
mod tests {
    use super::*;
    use polis_sbc_controller_embedded_api::{Counters, Key, timing::BootTiming};
    fn statuses() -> (Status, Status, Status) {
        let before = Status {
            timing: BootTiming::default(),
            boot_mode: BootMode::Normal,
            uptime_us: 100,
            power_on: false,
            cycle: None,
            uart_baud: 115200,
            uart_tx_busy: false,
            uart_rx_offset: 0,
            counters: Counters::default(),
        };
        let enabled = Status {
            uptime_us: 1200,
            power_on: true,
            timing: BootTiming {
                key: Some(Key { session: 2, id: 5 }),
                enable_before_us: 1100,
                enable_after_us: 1102,
                ..Default::default()
            },
            ..before
        };
        let last = Status {
            uptime_us: 10000,
            timing: BootTiming {
                entry_count: 1,
                entry_observed_us: Some(9000),
                ..enabled.timing
            },
            ..enabled
        };
        (before, enabled, last)
    }
    #[test]
    fn actual_edge_not_scheduled_deadline_and_no_clock_mixing() {
        let (b, e, s) = statuses();
        let m = measurement(b, e, s).unwrap();
        assert_eq!(m["enable_to_marker_observed_us"], 7898);
        assert_eq!(m["enable_bracket_width_us"], 2);
    }
    #[test]
    fn rejects_stale_missing_duplicate_and_lost_capture() {
        let (b, e, s) = statuses();
        let mut bad = s;
        bad.timing.entry_count = 0;
        assert!(measurement(b, e, bad).is_err());
        bad = s;
        bad.timing.entry_count = 2;
        assert!(measurement(b, e, bad).is_err());
        bad = s;
        bad.timing.key = None;
        assert!(measurement(b, e, bad).is_err());
        bad = s;
        bad.counters.uart_errors = 1;
        assert!(measurement(b, e, bad).is_err());
        bad = s;
        bad.timing.entry_observed_us = Some(50);
        assert!(measurement(b, e, bad).is_err());
        let mut before = b;
        before.timing = e.timing;
        assert!(measurement(before, e, s).is_err());
    }
}
