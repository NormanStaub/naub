//! App icons, made when an app first shows up rather than at firmware build.
//!
//! The firmware's baked set (`demos/switcher_data.py`) only knows the apps that
//! were open when `gen_switcher.py` last ran; everything else drew as a letter
//! in a box. So the daemon pulls the icon from the session's own exe, encodes
//! it in exactly the form the shelf blits, and pushes it with `aicon`
//! (PROTOCOL.md §11).
//!
//! The panel holds pushed icons in RAM and loses them on a reload, which is
//! fine: this end keeps them, in `%LOCALAPPDATA%\naub\icons\`, and sends them
//! again on every connect. Extraction loads shell code and touches the disk, so
//! it runs on a short-lived thread and never in the meter loop.

use std::collections::{HashSet, VecDeque};
use std::path::PathBuf;
use std::sync::mpsc::{channel, Receiver, Sender};
use std::time::{Duration, Instant};

#[cfg(windows)]
use windows::core::{PCWSTR, HSTRING};
#[cfg(windows)]
use windows::Win32::Foundation::SIZE;
#[cfg(windows)]
use windows::Win32::Graphics::Gdi::{
    CreateCompatibleDC, DeleteDC, DeleteObject, GetDIBits, GetObjectW, BITMAP, BITMAPINFO,
    BITMAPINFOHEADER, BI_RGB, DIB_RGB_COLORS, HBITMAP,
};
#[cfg(windows)]
use windows::Win32::System::Com::{CoInitializeEx, CoUninitialize, COINIT_APARTMENTTHREADED};
#[cfg(windows)]
use windows::Win32::UI::Shell::{
    IShellItemImageFactory, SHCreateItemFromParsingName, SIIGBF_ICONONLY,
};

/// Must match `device/drawer.TIERS` (the sizes) and `N_PLANES`.
const TIERS: [usize; 2] = [32, 24];
const PLANES: usize = 4;
/// Screen row the shelf draws at: `(drawer.PANEL_P0 + page0) * 8`. Both tiers
/// share it. The dither is keyed to screen coordinates, so this is part of the
/// format and not just a position.
const OY: usize = 24;
/// Two tiers, four planes each.
pub const ICON_BYTES: usize = (4 * 32 + 3 * 24) * PLANES;

/// Bump to orphan every cached icon when the encoding changes.
const FORMAT: u64 = 1;
/// Must equal `device/drawer.LIVE_MAX`: both ends drop the oldest at this
/// count, which is how this end knows to send an evicted app again.
const LIVE_MAX: usize = 32;
/// Space between icon lines, so a reconnect with a full shelf does not starve
/// the meters or sit on the write timeout. MEASURED: see PROTOCOL.md §11.
const GAP: Duration = Duration::from_millis(100);

const BAYER: [[u8; 4]; 4] = [[0, 8, 2, 10], [12, 4, 14, 6], [3, 11, 1, 9], [15, 7, 13, 5]];

/// Gray pixels (255 = white) -> this tier's planes, the same bytes as
/// `gen_switcher.block`. The two are held together by `matches_python` below.
fn encode_tier(gray: &[u8], size: usize, out: &mut Vec<u8>) {
    let pages = size >> 3;
    let start = out.len();
    out.resize(start + pages * size * PLANES, 0);
    for yy in 0..size {
        let y = OY + yy;
        for x in 0..size {
            let d = (1.0 - gray[yy * size + x] as f64 / 255.0) * PLANES as f64;
            let mut base = d as usize;
            if (BAYER[y % 4][x % 4] as f64 + 0.5) / 16.0 < d - base as f64 {
                base += 1;
            }
            let p = (x + y) % PLANES;
            for k in 0..PLANES {
                if (k + PLANES - p) % PLANES < base {
                    out[start + k * pages * size + (yy >> 3) * size + x] |= 1 << (yy & 7);
                }
            }
        }
    }
}

/// The icon at exactly `size`, as gray over white. Asking the shell for the
/// size rather than scaling a big one gets the art drawn for that size, where
/// the exe has it. `None` for no usable image -- including an all-white one,
/// which would be a worse answer than the placeholder.
///
/// Caller must have COM initialised on this thread.
#[cfg(windows)]
fn gray_icon(exe: &str, size: usize) -> Option<Vec<u8>> {
    unsafe {
        let factory: IShellItemImageFactory =
            SHCreateItemFromParsingName(PCWSTR(HSTRING::from(exe).as_ptr()), None).ok()?;
        let want = SIZE { cx: size as i32, cy: size as i32 };
        let bmp = factory.GetImage(want, SIIGBF_ICONONLY).ok()?;
        let gray = bitmap_gray(bmp, size);
        let _ = DeleteObject(bmp);
        gray.filter(|g| g.iter().any(|&v| v != 255))
    }
}

#[cfg(windows)]
unsafe fn bitmap_gray(bmp: HBITMAP, size: usize) -> Option<Vec<u8>> {
    let mut info = BITMAP::default();
    let got = GetObjectW(
        bmp,
        std::mem::size_of::<BITMAP>() as i32,
        Some(&mut info as *mut BITMAP as *mut _),
    );
    if got == 0 || info.bmWidth <= 0 || info.bmHeight <= 0 {
        return None;
    }
    let (w, h) = (info.bmWidth as usize, info.bmHeight as usize);
    let mut bgra = vec![0u8; w * h * 4];
    let mut bi = BITMAPINFO::default();
    bi.bmiHeader = BITMAPINFOHEADER {
        biSize: std::mem::size_of::<BITMAPINFOHEADER>() as u32,
        biWidth: w as i32,
        biHeight: -(h as i32), // top-down
        biPlanes: 1,
        biBitCount: 32,
        biCompression: BI_RGB.0,
        ..Default::default()
    };
    let dc = CreateCompatibleDC(None);
    let lines = GetDIBits(
        dc,
        bmp,
        0,
        h as u32,
        Some(bgra.as_mut_ptr() as *mut _),
        &mut bi,
        DIB_RGB_COLORS,
    );
    let _ = DeleteDC(dc);
    if lines == 0 {
        return None;
    }
    Some(flatten(&bgra, w, h, size))
}

/// The icon of an app bundle or file at exactly `size`, as gray over white:
/// `iconForFile` picks the art drawn nearest that size, and drawing into a
/// private bitmap context needs no window and no main thread.
#[cfg(target_os = "macos")]
fn gray_icon(path: &str, size: usize) -> Option<Vec<u8>> {
    use objc2::AnyThread;
    use objc2_app_kit::{
        NSBitmapImageRep, NSCalibratedRGBColorSpace, NSGraphicsContext, NSWorkspace,
    };
    use objc2_foundation::{NSPoint, NSRect, NSSize, NSString};

    let icon = NSWorkspace::sharedWorkspace().iconForFile(&NSString::from_str(path));
    let px = size as isize;
    let rep = unsafe {
        NSBitmapImageRep::initWithBitmapDataPlanes_pixelsWide_pixelsHigh_bitsPerSample_samplesPerPixel_hasAlpha_isPlanar_colorSpaceName_bytesPerRow_bitsPerPixel(
            NSBitmapImageRep::alloc(),
            std::ptr::null_mut(),
            px,
            px,
            8,
            4,
            true,
            false,
            NSCalibratedRGBColorSpace,
            px * 4,
            32,
        )
    }?;
    let ctx = NSGraphicsContext::graphicsContextWithBitmapImageRep(&rep)?;
    NSGraphicsContext::saveGraphicsState_class();
    NSGraphicsContext::setCurrentContext(Some(&ctx));
    let rect = NSRect::new(NSPoint::new(0.0, 0.0), NSSize::new(size as f64, size as f64));
    icon.drawInRect(rect);
    ctx.flushGraphics();
    NSGraphicsContext::restoreGraphicsState_class();

    let data = rep.bitmapData();
    if data.is_null() {
        return None;
    }
    // Premultiplied RGBA, top row first; `flatten` wants its colours as BGRA.
    let mut bgra = unsafe { std::slice::from_raw_parts(data, size * size * 4) }.to_vec();
    // Nothing got drawn. `flatten` reads all-zero alpha as an opaque bitmap
    // (true of the Windows shell), which here would be a solid black icon.
    if bgra.chunks_exact(4).all(|p| p[3] == 0) {
        return None;
    }
    bgra.chunks_exact_mut(4).for_each(|p| p.swap(0, 2));
    Some(flatten(&bgra, size, size, size)).filter(|g| g.iter().any(|&v| v != 255))
}

/// BGRA -> gray over white, centred on a `size` square.
///
/// The shell does not promise which alpha it hands back, so it is read off the
/// pixels: a colour brighter than its own alpha cannot be premultiplied. No
/// alpha at all (every pixel 0) is an opaque bitmap, not an invisible one.
fn flatten(bgra: &[u8], w: usize, h: usize, size: usize) -> Vec<u8> {
    let px = || bgra.chunks_exact(4);
    let opaque = px().all(|p| p[3] == 0);
    let straight = px().any(|p| p[0].max(p[1]).max(p[2]) > p[3]);
    let mut out = vec![255u8; size * size];
    let (ox, oy) = ((size as isize - w as isize) / 2, (size as isize - h as isize) / 2);
    for (i, p) in px().enumerate() {
        let (x, y) = ((i % w) as isize + ox, (i / w) as isize + oy);
        if x < 0 || y < 0 || x >= size as isize || y >= size as isize {
            continue;
        }
        let a = if opaque { 255 } else { p[3] as u32 };
        let over = |c: u8| -> u32 {
            let c = c as u32;
            if opaque {
                c
            } else if straight {
                (c * a + 255 * (255 - a)) / 255
            } else {
                (c + 255 - a).min(255)
            }
        };
        // PIL's "L" weights, which is what the baked icons were made with.
        let l = (over(p[2]) * 19595 + over(p[1]) * 38470 + over(p[0]) * 7471 + 0x8000) >> 16;
        out[y as usize * size + x as usize] = l.min(255) as u8;
    }
    out
}

/// Both tiers of `exe`'s icon, encoded; straight from the shell, no cache.
/// COM must be up on this thread.
fn render(exe: &str) -> Option<Vec<u8>> {
    let mut out = Vec::with_capacity(ICON_BYTES);
    for size in TIERS {
        encode_tier(&gray_icon(exe, size)?, size, &mut out);
    }
    Some(out)
}

// ---- the disk cache ---------------------------------------------------------

fn cache_path(key: &str) -> Option<PathBuf> {
    // A pid-fallback key or anything else unfit for a file name just goes
    // uncached; it still works.
    if key.is_empty() || key.contains(['\\', '/', ':', '*', '?', '"', '<', '>', '|']) {
        return None;
    }
    let base = dirs::data_local_dir()?;
    Some(base.join("naub").join("icons").join(format!("{key}.bin")))
}

/// Which build of which exe a cached icon came from: an update can change the
/// art, and FNV-1a is plenty to notice that.
fn fingerprint(exe: &str) -> u64 {
    let mtime = std::fs::metadata(exe)
        .and_then(|m| m.modified())
        .ok()
        .and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok())
        .map_or(0, |d| d.as_nanos() as u64);
    let mut h = 0xcbf2_9ce4_8422_2325u64 ^ FORMAT;
    for b in exe.bytes().chain(mtime.to_le_bytes()) {
        h = (h ^ b as u64).wrapping_mul(0x0000_0100_0000_01b3);
    }
    h
}

fn cached(key: &str, exe: &str) -> Option<Vec<u8>> {
    let raw = std::fs::read(cache_path(key)?).ok()?;
    if raw.len() != 8 + ICON_BYTES || raw[..8] != fingerprint(exe).to_le_bytes() {
        return None;
    }
    Some(raw[8..].to_vec())
}

/// Best effort: a failed write only means extracting again next time.
fn store(key: &str, exe: &str, blob: &[u8]) {
    let Some(path) = cache_path(key) else { return };
    if let Some(dir) = path.parent() {
        let _ = std::fs::create_dir_all(dir);
    }
    let mut raw = fingerprint(exe).to_le_bytes().to_vec();
    raw.extend_from_slice(blob);
    let _ = std::fs::write(path, raw);
}

fn load(key: &str, exe: &str) -> Option<Vec<u8>> {
    if let Some(blob) = cached(key, exe) {
        return Some(blob);
    }
    // STA, and the thread's own: the shell's icon handlers expect one, and this
    // thread is gone as soon as the icon is.
    #[cfg(windows)]
    let blob = unsafe {
        let up = CoInitializeEx(None, COINIT_APARTMENTTHREADED).is_ok();
        let blob = render(exe);
        if up {
            CoUninitialize();
        }
        blob
    };
    // This thread has no pool of its own for AppKit to autorelease into.
    #[cfg(target_os = "macos")]
    let blob = objc2::rc::autoreleasepool(|_| render(exe));
    if let Some(b) = &blob {
        store(key, exe, b);
    }
    blob
}

// ---- getting them to the device ---------------------------------------------

/// Which icons the device has, and the one being fetched.
///
/// `sent` mirrors the device's first-in-first-out store, which is what lets an
/// app the device evicted be noticed and sent again. One fetch at a time: a
/// reconnect with a full shelf should not spawn a thread per app.
pub struct Pusher {
    sent: VecDeque<String>,
    /// No usable icon. Asked again after a reconnect, not once a second.
    none: HashSet<String>,
    tx: Sender<(String, Option<Vec<u8>>)>,
    rx: Receiver<(String, Option<Vec<u8>>)>,
    busy: bool,
    ready: Option<(String, Vec<u8>)>,
    next_send: Instant,
}

impl Pusher {
    pub fn new() -> Self {
        let (tx, rx) = channel();
        Self {
            sent: VecDeque::new(),
            none: HashSet::new(),
            tx,
            rx,
            busy: false,
            ready: None,
            next_send: Instant::now(),
        }
    }

    /// The device lost its store: it reloaded, or this is a new connection.
    pub fn reset(&mut self) {
        self.sent.clear();
        self.none.clear();
    }

    fn known(&self, key: &str) -> bool {
        self.none.contains(key) || self.sent.iter().any(|k| k == key)
    }

    /// The next app in `apps` that wants fetching, if nothing else is underway.
    pub fn next_wanted<'a>(&self, apps: &'a [String]) -> Option<&'a String> {
        if self.busy || self.ready.is_some() {
            return None;
        }
        apps.iter().find(|k| !self.known(k))
    }

    pub fn mark_none(&mut self, key: &str) {
        self.none.insert(key.to_string());
    }

    pub fn fetch(&mut self, key: &str, exe: String) {
        let key = key.to_string();
        let tx = self.tx.clone();
        self.busy = true;
        std::thread::spawn(move || {
            let blob = load(&key, &exe);
            let _ = tx.send((key, blob));
        });
    }

    /// The next `aicon` line, once one is ready and its turn has come.
    pub fn poll(&mut self, now: Instant) -> Option<String> {
        if let Ok((key, blob)) = self.rx.try_recv() {
            self.busy = false;
            match blob {
                Some(b) => self.ready = Some((key, b)),
                None => self.mark_none(&key),
            }
        }
        if self.ready.is_none() || now < self.next_send {
            return None;
        }
        let (key, blob) = self.ready.take()?;
        self.next_send = now + GAP;
        let line = crate::protocol::aicon(&key, &blob);
        if !self.sent.iter().any(|k| *k == key) {
            if self.sent.len() >= LIVE_MAX {
                self.sent.pop_front();
            }
            self.sent.push_back(key);
        }
        Some(line)
    }
}

/// `--icon`: what would this exe's icon look like, without a device.
pub fn dump(exe: &str) -> bool {
    #[cfg(windows)]
    unsafe {
        let _ = CoInitializeEx(None, COINIT_APARTMENTTHREADED);
    }
    let t0 = Instant::now();
    let Some(blob) = render(exe) else {
        println!("no usable icon for {exe}");
        return false;
    };
    println!("{exe}: {} bytes in {} ms", blob.len(), t0.elapsed().as_millis());
    // Time-averaged like the panel shows it: planes per pixel lit -> a shade.
    let (size, pages) = (TIERS[0], TIERS[0] >> 3);
    for y in 0..size {
        let row: String = (0..size)
            .map(|x| {
                let on = (0..PLANES)
                    .filter(|k| {
                        blob[k * pages * size + (y >> 3) * size + x] & (1 << (y & 7)) != 0
                    })
                    .count();
                [' ', '.', '+', '#', '@'][on]
            })
            .flat_map(|c| [c, c])
            .collect();
        println!("{row}");
    }
    true
}

#[cfg(test)]
mod tests {
    use super::*;

    fn pattern(size: usize) -> Vec<u8> {
        (0..size * size)
            .map(|i| (((i % size) * 7 + (i / size) * 13) % 256) as u8)
            .collect()
    }

    /// Byte for byte against host/gen_frc_golden.py, which runs the Python
    /// encoder the baked icons come from.
    #[test]
    fn matches_python() {
        let mut out = Vec::new();
        for size in TIERS {
            encode_tier(&pattern(size), size, &mut out);
        }
        assert_eq!(out.len(), ICON_BYTES);
        assert_eq!(&out[..], &include_bytes!("frc_golden.bin")[..]);
    }

    #[test]
    fn flatten_composites_over_white() {
        // straight alpha: red at half, which a premultiplied bitmap cannot hold
        let g = flatten(&[0, 0, 255, 128], 1, 1, 1);
        assert_eq!(g[0], ((255u32 * 19595 + 127 * (38470 + 7471) + 0x8000) >> 16) as u8);
        // premultiplied: black at half reads as the mid gray
        assert_eq!(flatten(&[0, 0, 0, 128], 1, 1, 1)[0], 127);
        // all alpha 0: opaque, not invisible
        assert_eq!(flatten(&[10, 10, 10, 0], 1, 1, 1)[0], 10);
    }

    #[test]
    fn evicts_with_the_device() {
        let mut p = Pusher::new();
        let mut now = Instant::now();
        for i in 0..=LIVE_MAX {
            p.ready = Some((format!("app{i}"), vec![0; ICON_BYTES]));
            now += GAP;
            assert!(p.poll(now).unwrap().starts_with("aicon "));
        }
        assert!(!p.known("app0"), "the oldest should be wanted again");
        assert!(p.known("app1"));
    }
}
