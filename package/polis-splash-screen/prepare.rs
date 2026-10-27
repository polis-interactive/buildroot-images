// SPDX-License-Identifier: GPL-2.0-only
//! Host-only kernel integration and persistent logo preparation. No crates.
use std::{
    env, fs,
    path::Path,
    process::{self, Command},
};

fn rgb(ppm: &str) -> Result<Vec<u8>, String> {
    let clean: String = ppm
        .lines()
        .map(|line| format!("{}\n", line.split('#').next().unwrap_or("")))
        .collect();
    let mut words = clean.split_whitespace();
    for expected in ["P3", "512", "512", "255"] {
        if words.next() != Some(expected) {
            return Err("expected a 512x512 ASCII RGB PPM with maxval 255".into());
        }
    }
    let pixels: Vec<u8> = words
        .map(|word| {
            word.parse::<u8>()
                .map_err(|_| format!("invalid RGB channel: {word}"))
        })
        .collect::<Result<_, _>>()?;
    if pixels.len() != 512 * 512 * 3 {
        return Err("expected exactly 512x512 RGB pixels".into());
    }
    Ok(pixels)
}

fn write_if_changed(path: &Path, data: &[u8]) -> Result<(), String> {
    if fs::read(path).ok().as_deref() == Some(data) {
        return Ok(());
    }
    fs::write(path, data).map_err(|e| format!("{}: {e}", path.display()))
}

fn patch(kernel: &Path, patch: &Path, reverse: bool, dry: bool) -> Result<bool, String> {
    let mut cmd = Command::new("patch");
    cmd.current_dir(kernel)
        .args(["--batch", "--fuzz=0", "-p1", "--no-backup-if-mismatch"])
        .arg("--forward");
    // --batch alone automatically undoes --reverse on an unpatched tree,
    // falsely reporting success. --forward forbids that direction reversal.
    if reverse {
        cmd.arg("--reverse");
    }
    if dry {
        cmd.arg("--dry-run");
    }
    let result = cmd
        .arg("-i")
        .arg(patch)
        .output()
        .map_err(|e| format!("patch: {e}"))?;
    if !dry && !result.status.success() {
        return Err(format!(
            "kernel integration failed: {}{}",
            String::from_utf8_lossy(&result.stdout),
            String::from_utf8_lossy(&result.stderr)
        ));
    }
    Ok(result.status.success())
}

fn prepare(repo: &Path, kernel: &Path) -> Result<(), String> {
    let package = repo.join("package/polis-splash-screen");
    // Validate all payloads before touching the kernel tree.
    let logo_path = repo.join("profiles/groups/rpi4-video/splash/polis-splash.ppm");
    let logo = rgb(&fs::read_to_string(&logo_path).map_err(|e| e.to_string())?)?;
    let names = [
        "polis_splash_screen.rs",
        "drm.rs",
        "lifecycle.rs",
        "render.rs",
    ];
    let sources = names
        .iter()
        .map(|name| fs::read(package.join("src").join(name)))
        .collect::<Result<Vec<_>, _>>()
        .map_err(|e| e.to_string())?;
    let integration = package.join("kernel.patch");
    if !patch(kernel, &integration, true, true)? {
        if !patch(kernel, &integration, false, true)? {
            return Err("kernel.patch does not apply cleanly (or is partially applied); use linux-dirclean before rebuilding; no files changed".into());
        }
        patch(kernel, &integration, false, false)?;
    }
    let target = kernel.join("drivers/gpu/drm/vc4/polis-splash-screen");
    fs::create_dir_all(&target).map_err(|e| e.to_string())?;
    for (name, source) in names.iter().zip(sources) {
        write_if_changed(&target.join(name), &source)?;
    }
    write_if_changed(&target.join("polis-logo.rgb"), &logo)?;
    write_if_changed(
        &target.join("Makefile"),
        b"# SPDX-License-Identifier: GPL-2.0-only\nobj-y += polis_splash_screen.o\n",
    )?;
    println!("polis-splash-screen: kernel sources and persistent RGB logo ready");
    Ok(())
}

fn main() {
    let args: Vec<_> = env::args_os().collect();
    if args.len() != 3 {
        eprintln!("usage: polis-splash-prepare REPOSITORY KERNEL_SOURCE");
        process::exit(2);
    }
    let result = fs::canonicalize(&args[1])
        .map_err(|e| e.to_string())
        .and_then(|repo| {
            fs::canonicalize(&args[2])
                .map_err(|e| e.to_string())
                .and_then(|kernel| prepare(&repo, &kernel))
        });
    if let Err(error) = result {
        eprintln!("polis-splash-screen: {error}");
        process::exit(1);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn refuses_bad_ppm_without_truncation_or_channel_wrapping() {
        for ppm in [
            "P6 512 512 255",
            "P3 1 1 255 0 0 0",
            "P3 512 512 255 0 0 0",
            "P3 512 512 255 -1",
            "P3 512 512 255 256",
        ] {
            assert!(rgb(ppm).is_err());
        }
    }
    #[test]
    fn persistent_pixels_match_source() {
        let ppm = format!(
            "P3\n# artwork\n512 512\n255\n{}",
            "12 34 56\n".repeat(512 * 512)
        );
        let bytes = rgb(&ppm).unwrap();
        assert_eq!(bytes.len(), 512 * 512 * 3);
        assert!(bytes.chunks_exact(3).all(|p| p == [12, 34, 56]));
    }
}
