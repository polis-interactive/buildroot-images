//! One-shot boot diagnostics collector. No GPIO or UART access.
mod diagnostics;

fn main() {
    if std::env::args().skip(1).any(|arg| arg == "--help") {
        println!("Usage: polis-boot-diagnostics\nWait for writable /data, save this boot's diagnostic capture, and exit.");
        return;
    }
    let uptime = std::fs::read_to_string("/proc/uptime")
        .ok()
        .and_then(|text| text.split_whitespace().next().map(str::to_owned))
        .unwrap_or_else(|| "unknown".into());
    let log = format!("POLIS_BOOT_DIAGNOSTICS_START uptime={uptime}\n");
    print!("{log}");
    diagnostics::run(&log);
}
