//! Persist one boot capture, without putting storage I/O on the LED task.
use std::{
    fs::{self, File},
    io::{self, Write},
    path::{Path, PathBuf},
    process::Command,
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc,
    },
    thread,
    time::Duration,
};

const DATA: &str = "/data";

// The directory existing is not enough: it also exists in the read-only image.
// Check both per-mount and superblock options, and require a block-backed ext4.
fn data_is_mounted(mountinfo: &str) -> bool {
    mountinfo.lines().any(|line| {
        let Some((mount, filesystem)) = line.split_once(" - ") else {
            return false;
        };
        let mount: Vec<_> = mount.split_whitespace().collect();
        let filesystem: Vec<_> = filesystem.split_whitespace().collect();
        mount.len() >= 6
            && filesystem.len() >= 3
            && mount[2]
                .split_once(':')
                .is_some_and(|(major, _)| major != "0")
            && mount[3] == "/"
            && mount[4] == DATA
            && mount[5].split(',').any(|option| option == "rw")
            && filesystem[0] == "ext4"
            && filesystem[2].split(',').any(|option| option == "rw")
    })
}

fn collect_command(
    files: &mut Vec<(&'static str, Vec<u8>)>,
    name: &'static str,
    error_name: &'static str,
    command: &mut Command,
) {
    match command.output() {
        Ok(output) if output.status.success() => files.push((name, output.stdout)),
        result => {
            let detail = match result {
                Ok(output) => format!(
                    "{command:?}: {}\nstdout:\n{}\nstderr:\n{}",
                    output.status,
                    String::from_utf8_lossy(&output.stdout),
                    String::from_utf8_lossy(&output.stderr)
                ),
                Err(error) => format!("Could not run {command:?}: {error}\n"),
            };
            // Unsupported firmware or missing tools must not discard GPIO
            // timings, block the remaining captures, or prevent the saved LED.
            files.push((error_name, detail.into_bytes()));
        }
    }
}

fn collect(gpio_log: &str) -> Vec<(&'static str, Vec<u8>)> {
    let mut files = vec![("gpio.log", gpio_log.as_bytes().to_vec())];
    for (name, source) in [
        ("cmdline.txt", "/proc/cmdline"),
        ("mountinfo.txt", "/proc/self/mountinfo"),
        ("uptime.txt", "/proc/uptime"),
        ("kernel-version.txt", "/proc/version"),
    ] {
        let content = fs::read(source)
            .unwrap_or_else(|error| format!("Could not read {source}: {error}\n").into_bytes());
        files.push((name, content));
    }
    // BusyBox dmesg reads the ring buffer without clearing it. No UART output.
    collect_command(
        &mut files,
        "dmesg.log",
        "dmesg-error.txt",
        &mut Command::new("dmesg"),
    );
    // Snapshot only: --follow would wait indefinitely. vclog reads the retained
    // VideoCore buffer; this is not a complete EEPROM bootloader UART trace.
    collect_command(
        &mut files,
        "firmware.log",
        "firmware-error.txt",
        Command::new("/usr/bin/vclog").arg("--msg"),
    );
    // All commands below query existing state. No EEPROM writes or UART I/O.
    for (name, error_name, args) in [
        (
            "firmware-version.txt",
            "firmware-version-error.txt",
            &["version"][..],
        ),
        (
            "bootloader-config.txt",
            "bootloader-config-error.txt",
            &["bootloader_config"][..],
        ),
        (
            "bootloader-version.txt",
            "bootloader-version-error.txt",
            &["bootloader_version"][..],
        ),
        (
            "firmware-config-int.txt",
            "firmware-config-int-error.txt",
            &["get_config", "int"][..],
        ),
        (
            "firmware-config-str.txt",
            "firmware-config-str-error.txt",
            &["get_config", "str"][..],
        ),
    ] {
        collect_command(
            &mut files,
            name,
            error_name,
            Command::new("/usr/bin/vcgencmd").args(args),
        );
    }
    files
}

fn save(data: &Path, boot_id: &str, files: &[(&str, Vec<u8>)]) -> io::Result<PathBuf> {
    if boot_id.len() != 36
        || !boot_id
            .bytes()
            .all(|byte| byte.is_ascii_hexdigit() || byte == b'-')
    {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "invalid kernel boot ID",
        ));
    }
    let parent = data.join("boot-diagnostics");
    fs::create_dir_all(&parent)?;
    let destination = parent.join(boot_id);
    if destination.is_dir() {
        // Keep the first completed capture if the probe restarts in this boot.
        File::open(&parent)?.sync_all()?;
        File::open(data)?.sync_all()?;
        return Ok(destination);
    }
    let pending = parent.join(format!(".{boot_id}.pending"));
    fs::create_dir_all(&pending)?;
    for (name, content) in files {
        let mut file = File::create(pending.join(name))?;
        file.write_all(content)?;
        file.sync_all()?;
    }
    File::open(&pending)?.sync_all()?;
    fs::rename(&pending, &destination)?;
    File::open(&parent)?.sync_all()?;
    File::open(data)?.sync_all()?;
    Ok(destination)
}

pub fn start(gpio_log: String, exported: Arc<AtomicBool>) {
    // Called only after the ready edge or the terminal UART error. A dedicated
    // thread keeps dmesg, SD writes, and fsync off the single-thread Tokio loop.
    if let Err(error) = thread::Builder::new()
        .name("boot-log-export".into())
        .spawn(move || {
            let mut last_error = String::new();
            loop {
                let result = (|| {
                    let mounts = fs::read_to_string("/proc/self/mountinfo")?;
                    if !data_is_mounted(&mounts) {
                        return Ok(None);
                    }
                    let boot_id = fs::read_to_string("/proc/sys/kernel/random/boot_id")?;
                    save(Path::new(DATA), boot_id.trim(), &collect(&gpio_log)).map(Some)
                })();
                match result {
                    Ok(Some(path)) => {
                        exported.store(true, Ordering::Release);
                        println!("POLIS_BOOT_LOGS_SAVED path={}", path.display());
                        return;
                    }
                    Ok(None) => {}
                    Err(error) => {
                        let message = format!("{error}");
                        if message != last_error {
                            eprintln!(
                                "POLIS_BOOT_LOGS_ERROR error={message}; retrying every second"
                            );
                            last_error = message;
                        }
                    }
                }
                thread::sleep(Duration::from_secs(1));
            }
        })
    {
        eprintln!("POLIS_BOOT_LOGS_ERROR could not start exporter: {error}");
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    const BOOT_ID: &str = "12345678-abcd-1234-abcd-123456789abc";

    #[test]
    fn command_failure_preserves_output_and_allows_later_captures() {
        let mut files = vec![("gpio.log", b"READY\n".to_vec())];
        collect_command(
            &mut files,
            "firmware.log",
            "firmware-error.txt",
            Command::new("sh").args(["-c", "printf partial; printf unavailable >&2; exit 1"]),
        );
        collect_command(
            &mut files,
            "bootloader-config.txt",
            "bootloader-config-error.txt",
            Command::new("sh").args(["-c", "printf 'BOOT_ORDER=0xf41\\n'"]),
        );
        assert_eq!(files[0], ("gpio.log", b"READY\n".to_vec()));
        assert_eq!(files[1].0, "firmware-error.txt");
        let error = String::from_utf8_lossy(&files[1].1);
        assert!(error.contains("partial"));
        assert!(error.contains("unavailable"));
        assert_eq!(
            files[2],
            ("bootloader-config.txt", b"BOOT_ORDER=0xf41\n".to_vec())
        );
    }

    #[test]
    fn missing_firmware_tool_is_saved_as_an_error() {
        let mut files = vec![];
        collect_command(
            &mut files,
            "firmware.log",
            "firmware-error.txt",
            &mut Command::new("/nonexistent-polis-test-directory/vclog"),
        );
        assert_eq!(files.len(), 1);
        assert_eq!(files[0].0, "firmware-error.txt");
        assert!(String::from_utf8_lossy(&files[0].1).contains("Could not run"));
    }

    #[test]
    fn requires_the_actual_writable_data_mount() {
        let mounted = "35 20 179:3 / /data rw,relatime - ext4 /dev/mmcblk0p3 rw\n";
        assert!(data_is_mounted(mounted));
        for invalid in [
            String::new(),
            mounted.replace("/data", "/data-other"),
            mounted.replace("rw,relatime", "ro,relatime"),
            mounted.replace("mmcblk0p3 rw", "mmcblk0p3 ro"),
            mounted.replace("ext4", "tmpfs"),
            mounted.replace("179:3", "0:23"),
            mounted.replace("179:3 / /data", "179:3 /subdir /data"),
        ] {
            assert!(!data_is_mounted(&invalid), "accepted {invalid:?}");
        }
    }

    struct TestData(PathBuf);
    impl TestData {
        fn new() -> Self {
            let path = std::env::temp_dir().join(format!(
                "polis-logs-{}-{:?}",
                std::process::id(),
                thread::current().id()
            ));
            fs::create_dir(&path).unwrap();
            Self(path)
        }
    }
    impl Drop for TestData {
        fn drop(&mut self) {
            fs::remove_dir_all(&self.0).unwrap();
        }
    }

    #[test]
    fn saves_complete_capture_and_preserves_it_on_restart() {
        let data = TestData::new();
        let files = [
            ("gpio.log", b"START\nREADY\n".to_vec()),
            ("dmesg.log", b"kernel boot\n".to_vec()),
        ];
        let path = save(&data.0, BOOT_ID, &files).unwrap();
        assert_eq!(fs::read(path.join("gpio.log")).unwrap(), files[0].1);
        assert_eq!(fs::read(path.join("dmesg.log")).unwrap(), files[1].1);
        assert_eq!(fs::read_dir(path.parent().unwrap()).unwrap().count(), 1);
        save(&data.0, BOOT_ID, &[("gpio.log", b"restarted".to_vec())]).unwrap();
        assert_eq!(fs::read(path.join("gpio.log")).unwrap(), files[0].1);
        let next_boot = "87654321-abcd-1234-abcd-123456789abc";
        save(&data.0, next_boot, &files).unwrap();
        assert_eq!(fs::read_dir(path.parent().unwrap()).unwrap().count(), 2);
    }

    #[test]
    fn failed_write_leaves_no_completed_capture_and_can_retry() {
        let data = TestData::new();
        // A nonexistent subdirectory makes the second write fail after gpio.log.
        assert!(save(
            &data.0,
            BOOT_ID,
            &[("gpio.log", vec![]), ("missing/file", vec![])]
        )
        .is_err());
        assert!(!data.0.join("boot-diagnostics").join(BOOT_ID).exists());
        let path = save(&data.0, BOOT_ID, &[("gpio.log", b"retry".to_vec())]).unwrap();
        assert_eq!(fs::read(path.join("gpio.log")).unwrap(), b"retry");
    }

    #[test]
    fn rejects_invalid_boot_id_before_writing() {
        let data = TestData::new();
        assert!(save(&data.0, "../elsewhere", &[]).is_err());
        assert_eq!(fs::read_dir(&data.0).unwrap().count(), 0);
    }
}
