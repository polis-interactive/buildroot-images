// SPDX-License-Identifier: GPL-2.0-only
//! Allocation-free, bounds-checked XRGB8888 rendering. No fbdev interface.

pub(crate) const LOGO_SIDE: usize = 512;
pub(crate) const LOGO_BYTES: usize = LOGO_SIDE * LOGO_SIDE * 3;
pub(crate) const MAX_WIDTH: usize = 4096;
pub(crate) const MAX_HEIGHT: usize = 2160;

/// Returns the mapped byte count, rejecting invalid geometry before any writes.
pub(crate) fn layout(width: usize, height: usize, pitch: usize) -> Option<usize> {
    if width == 0 || height == 0 || width > MAX_WIDTH || height > MAX_HEIGHT {
        return None;
    }
    if pitch < width.checked_mul(4)? {
        return None;
    }
    let size = pitch.checked_mul(height)?;
    // Bound padded allocations as well as visible geometry.
    (size <= 64 * 1024 * 1024).then_some(size)
}

/// Paint a black background and centered logo, scaling down only when needed.
/// The caller supplies a CPU mapping of a *new*, not-yet-scanned-out buffer.
pub(crate) fn paint(
    dst: &mut [u8],
    width: usize,
    height: usize,
    pitch: usize,
    rgb: &[u8],
) -> Option<()> {
    let size = layout(width, height, pitch)?;
    if dst.len() < size || rgb.len() != LOGO_BYTES {
        return None;
    }
    dst[..size].fill(0);
    let side = LOGO_SIDE.min(width).min(height);
    let left = (width - side) / 2;
    let top = (height - side) / 2;
    for y in 0..side {
        for x in 0..side {
            let src = ((y * LOGO_SIDE / side) * LOGO_SIDE + x * LOGO_SIDE / side) * 3;
            let out = (top + y) * pitch + (left + x) * 4;
            // DRM_FORMAT_XRGB8888 is byte ordered B,G,R,X on this ARM64 target.
            dst[out..out + 4].copy_from_slice(&[rgb[src + 2], rgb[src + 1], rgb[src], 0]);
        }
    }
    Some(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn centers_and_honors_pitch_without_writing_past_mapping() {
        let mut rgb = vec![0; LOGO_BYTES];
        rgb[..3].copy_from_slice(&[0x12, 0x34, 0x56]);
        let pitch = 520 * 4 + 16;
        let size = pitch * 516;
        let mut dst = vec![0xff; size + 16];
        paint(&mut dst, 520, 516, pitch, &rgb).unwrap();
        assert_eq!(
            &dst[2 * pitch + 4 * 4..2 * pitch + 5 * 4],
            &[0x56, 0x34, 0x12, 0]
        );
        assert!(dst[..pitch].iter().all(|b| *b == 0));
        assert!(dst[size..].iter().all(|b| *b == 0xff));
    }

    #[test]
    fn downscales_without_cropping() {
        let mut rgb = vec![0; LOGO_BYTES];
        let source = (256 * LOGO_SIDE + 256) * 3;
        rgb[source..source + 3].copy_from_slice(&[1, 2, 3]);
        let mut dst = [0xff; 16];
        paint(&mut dst, 2, 2, 8, &rgb).unwrap();
        assert_eq!(&dst[12..16], &[3, 2, 1, 0]);
    }

    #[test]
    fn rejects_bad_geometry_before_writing() {
        let rgb = vec![0; LOGO_BYTES];
        let mut dst = [0xaa; 16];
        for (w, h, p) in [
            (0, 2, 8),
            (2, 0, 8),
            (2, 2, 7),
            (2, 3, 8),
            (usize::MAX, 1, 8),
            (2, 2, usize::MAX),
        ] {
            assert_eq!(paint(&mut dst, w, h, p, &rgb), None);
            assert_eq!(dst, [0xaa; 16]);
        }
        assert_eq!(paint(&mut dst, 2, 2, 8, &rgb[..3]), None);
        assert_eq!(dst, [0xaa; 16]);
    }
}
