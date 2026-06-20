//! Program-icon extraction -> 1-bpp packed bitmap for the e-paper.
//!
//! Pipeline (mirrors the Python host): exe HICON -> draw over white onto a 32x32
//! BGRA DIB -> grayscale -> Floyd-Steinberg dither to black/white -> pack rows
//! (MSB = leftmost pixel, 1 = black ink). The panel is 1-bit; dithering its dots
//! is what makes icons read as grayscale. Returns None when the exe has no usable
//! icon (degenerate / blank) so the caller can clear the icon box.

use std::ffi::c_void;

use image::imageops::{dither, BiLevel};
use image::GrayImage;
use windows::core::PCWSTR;
use windows::Win32::Foundation::HANDLE;
use windows::Win32::Graphics::Gdi::{
    CreateCompatibleDC, CreateDIBSection, DeleteDC, DeleteObject, GdiFlush, GetDC, ReleaseDC,
    SelectObject, BITMAPINFO, BITMAPINFOHEADER, BI_RGB, DIB_RGB_COLORS, HDC,
};
use windows::Win32::UI::Shell::ExtractIconExW;
use windows::Win32::UI::WindowsAndMessaging::{DestroyIcon, DrawIconEx, DI_NORMAL, HICON};

pub const SIZE: u32 = 32; // matches the firmware's per-channel icon box

/// Returns (w, h, packed_1bpp) for an executable's icon, or None if it has none
/// or the result is degenerate (near-blank / solid).
pub fn icon_for(exe_path: &str) -> Option<(u32, u32, Vec<u8>)> {
    let gray = extract_gray(exe_path)?;
    let packed = dither_and_pack(gray);
    if degenerate(&packed) {
        None
    } else {
        Some((SIZE, SIZE, packed))
    }
}

/// Extract the exe's first icon, draw it over a white field, return 32x32 gray.
fn extract_gray(exe_path: &str) -> Option<GrayImage> {
    let wide: Vec<u16> = exe_path.encode_utf16().chain(std::iter::once(0)).collect();
    let mut large = HICON::default();
    let n = unsafe {
        ExtractIconExW(PCWSTR(wide.as_ptr()), 0, Some(&mut large), None, 1)
    };
    if n == 0 || large.is_invalid() {
        return None;
    }
    let result = unsafe { render_icon_to_gray(large) };
    unsafe {
        let _ = DestroyIcon(large);
    }
    result
}

unsafe fn render_icon_to_gray(hicon: HICON) -> Option<GrayImage> {
    let screen: HDC = GetDC(None);
    if screen.is_invalid() {
        return None;
    }
    let memdc = CreateCompatibleDC(screen);

    // Top-down (negative height) 32-bpp BGRA DIB.
    let mut bmi = BITMAPINFO::default();
    bmi.bmiHeader = BITMAPINFOHEADER {
        biSize: std::mem::size_of::<BITMAPINFOHEADER>() as u32,
        biWidth: SIZE as i32,
        biHeight: -(SIZE as i32),
        biPlanes: 1,
        biBitCount: 32,
        biCompression: BI_RGB.0 as u32,
        ..Default::default()
    };

    let mut bits: *mut c_void = std::ptr::null_mut();
    let hbmp = CreateDIBSection(memdc, &bmi, DIB_RGB_COLORS, &mut bits, HANDLE::default(), 0).ok();

    let gray = (|| {
        let hbmp = hbmp?;
        if bits.is_null() {
            return None;
        }
        let old = SelectObject(memdc, hbmp);
        let npx = (SIZE * SIZE) as usize;
        // White background, then alpha-blend the icon over it.
        std::ptr::write_bytes(bits as *mut u8, 0xFF, npx * 4);
        let _ = DrawIconEx(memdc, 0, 0, hicon, SIZE as i32, SIZE as i32, 0, None, DI_NORMAL);
        let _ = GdiFlush();

        let bgra = std::slice::from_raw_parts(bits as *const u8, npx * 4);
        let mut g = vec![0u8; npx];
        for i in 0..npx {
            let b = bgra[i * 4] as u32;
            let gr = bgra[i * 4 + 1] as u32;
            let r = bgra[i * 4 + 2] as u32;
            // Rec.601 luma.
            g[i] = ((r * 299 + gr * 587 + b * 114) / 1000) as u8;
        }
        SelectObject(memdc, old);
        GrayImage::from_raw(SIZE, SIZE, g)
    })();

    if let Some(h) = hbmp {
        let _ = DeleteObject(h);
    }
    let _ = DeleteDC(memdc);
    ReleaseDC(None, screen);
    gray
}

/// Floyd-Steinberg dither to 2 levels, then pack to 1-bpp (1 = black ink).
fn dither_and_pack(mut gray: GrayImage) -> Vec<u8> {
    dither(&mut gray, &BiLevel); // in-place FS; pixels become 0 or 255
    let stride = (SIZE as usize + 7) / 8;
    let mut data = vec![0u8; stride * SIZE as usize];
    for y in 0..SIZE {
        for x in 0..SIZE {
            if gray.get_pixel(x, y).0[0] == 0 {
                // black -> ink
                data[y as usize * stride + (x / 8) as usize] |= 0x80 >> (x % 8);
            }
        }
    }
    data
}

fn degenerate(packed: &[u8]) -> bool {
    let ink: u32 = packed.iter().map(|b| b.count_ones()).sum();
    let total = (packed.len() * 8) as f32;
    if total == 0.0 {
        return true;
    }
    let ratio = ink as f32 / total;
    ratio < 0.02 || ratio > 0.98
}
