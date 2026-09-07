//! The system output's real spectrum, by WASAPI loopback capture.
//!
//! The mixer's per-app meters invent their band shape, because Windows exposes
//! no per-session spectrum (PROTOCOL.md §11). The system output is different:
//! it can be *captured*, so the EQ screen's meter is a real analysis rather
//! than a plausible one.
//!
//! **Why not the endpoint's own peak meter.** `IAudioMeterInformation` on the
//! IMMDevice looked like the easy answer, and it is not: measured, dropping the
//! system fader from 1.00 to 0.30 moved it by x0.91 -- i.e. it reports the
//! stream *before* the volume, exactly like the per-session meters do, and it
//! gives one number rather than a spectrum. Loopback captures what is actually
//! being rendered.
//!
//! Run on demand: this is a capture thread and an open audio client on the
//! user's machine, and exactly one of four device screens ever draws it. See
//! `bridge::want_spectrum`.
//!
//! A port of `firmware/v2/host/sysmeter.py`, including its own FFT so the
//! daemon keeps its single-binary, no-runtime story (numpy is not available to
//! a 300 KB exe, and a 2048-point radix-2 transform is forty lines).

use std::sync::atomic::{AtomicBool, AtomicU32, Ordering};
use std::sync::mpsc;
use std::sync::{Arc, Mutex};
use std::thread::JoinHandle;
use std::time::Duration;


use windows::Win32::Media::Audio::{
    eConsole, eRender, IAudioCaptureClient, IAudioClient, IMMDeviceEnumerator, MMDeviceEnumerator,
    AUDCLNT_BUFFERFLAGS_SILENT, AUDCLNT_SHAREMODE_SHARED, AUDCLNT_STREAMFLAGS_LOOPBACK,
};
use windows::Win32::System::Com::{
    CoCreateInstance, CoInitializeEx, CoTaskMemFree, CLSCTX_ALL, COINIT_MULTITHREADED,
};

pub const FMIN: f64 = 20.0;
pub const FMAX: f64 = 20000.0;
/// ~43 ms at 48 kHz: enough for a 20 Hz bin to exist.
const FFT: usize = 2048;
/// Maps to 0 on the display ...
const FLOOR_DB: f64 = -66.0;
/// ... and this to full height.
const TOP_DB: f64 = -6.0;

pub struct SysMeter {
    nband: usize,
    buf: Arc<Mutex<Vec<f32>>>,
    run: Arc<AtomicBool>,
    rate: Arc<AtomicU32>,
    thread: Option<JoinHandle<()>>,
    name: Option<String>,
    err: Option<String>,
    win: Vec<f32>,
    edges: Vec<usize>,
}

impl SysMeter {
    pub fn new(nband: usize) -> Self {
        Self {
            nband,
            buf: Arc::new(Mutex::new(vec![0.0; FFT])),
            run: Arc::new(AtomicBool::new(false)),
            rate: Arc::new(AtomicU32::new(48000)),
            thread: None,
            name: None,
            err: None,
            // Hann window, precomputed once.
            win: (0..FFT)
                .map(|i| {
                    let x = std::f64::consts::PI * 2.0 * i as f64 / (FFT - 1) as f64;
                    (0.5 - 0.5 * x.cos()) as f32
                })
                .collect(),
            edges: Vec::new(),
        }
    }

    pub fn name(&self) -> Option<&str> {
        self.name.as_deref()
    }

    pub fn error(&self) -> Option<&str> {
        self.err.as_deref()
    }

    /// Open the loopback capture and start filling the rolling buffer.
    ///
    /// All the COM lives inside the worker thread: WASAPI interfaces are not
    /// `Send`, so the device is opened where it is used rather than handed
    /// across. The channel carries back only the sample rate and a name, or
    /// the reason it could not start.
    pub fn start(&mut self) -> bool {
        if self.thread.is_some() {
            return true;
        }
        let (tx, rx) = mpsc::channel::<std::result::Result<(u32, String), String>>();
        let buf = Arc::clone(&self.buf);
        let run = Arc::clone(&self.run);
        let rate = Arc::clone(&self.rate);
        run.store(true, Ordering::SeqCst);
        let handle = std::thread::spawn(move || capture_loop(tx, buf, run, rate));
        // Starting costs about as long as the mode wipe takes, so the screen
        // has its spectrum by the time it finishes sliding in.
        match rx.recv_timeout(Duration::from_millis(1500)) {
            Ok(Ok((hz, name))) => {
                self.rate.store(hz, Ordering::SeqCst);
                self.name = Some(name);
                self.err = None;
                self.edges = band_edges(self.nband, hz as f64);
                self.thread = Some(handle);
                true
            }
            Ok(Err(e)) => {
                self.run.store(false, Ordering::SeqCst);
                let _ = handle.join();
                self.err = Some(e);
                false
            }
            Err(_) => {
                self.run.store(false, Ordering::SeqCst);
                self.err = Some("loopback capture did not start in time".into());
                false
            }
        }
    }

    /// Stop the capture and let go of the endpoint. Joins, so a `start()`
    /// straight afterwards cannot end up with two threads filling one buffer.
    pub fn stop(&mut self) {
        self.run.store(false, Ordering::SeqCst);
        if let Some(t) = self.thread.take() {
            let _ = t.join();
        }
        if let Ok(mut b) = self.buf.lock() {
            b.iter_mut().for_each(|x| *x = 0.0);
        }
    }

    /// The capture is bound to one endpoint, so a device change kills the
    /// thread. Bring it back on the new default rather than silently returning
    /// the last spectrum captured from a device that is gone.
    pub fn restart_if_dead(&mut self) -> bool {
        match &self.thread {
            Some(t) if t.is_finished() => {}
            _ => return false,
        }
        let old = self.name.clone().unwrap_or_default();
        self.stop();
        if self.start() {
            crate::logging::line(&format!(
                "system spectrum reopened on {} (was {old})",
                self.name.clone().unwrap_or_default()
            ));
            return true;
        }
        false
    }

    /// `nband` values 0..1, low to high.
    pub fn bands(&self) -> Vec<f64> {
        if self.edges.is_empty() {
            return vec![0.0; self.nband];
        }
        let x: Vec<f32> = match self.buf.lock() {
            Ok(b) => b.clone(),
            Err(_) => return vec![0.0; self.nband],
        };
        let mut re: Vec<f64> = x.iter().zip(&self.win).map(|(v, w)| (v * w) as f64).collect();
        let mut im = vec![0.0f64; FFT];
        fft(&mut re, &mut im);
        // Magnitude of the one-sided spectrum, scaled the way numpy's
        // rfft * (2/N) is, so FLOOR_DB/TOP_DB keep meaning what they measured.
        let half = FFT / 2;
        let sp: Vec<f64> = (0..=half)
            .map(|i| (re[i] * re[i] + im[i] * im[i]).sqrt() * (2.0 / FFT as f64))
            .collect();
        let top = sp.len() - 1;
        (0..self.nband)
            .map(|i| {
                let a = self.edges[i].min(top);
                let b = self.edges[i + 1].max(a + 1).min(top + 1);
                let v = if b > a {
                    sp[a..b].iter().cloned().fold(0.0f64, f64::max)
                } else {
                    0.0
                };
                let db = if v > 1e-9 { 20.0 * v.log10() } else { -120.0 };
                ((db - FLOOR_DB) / (TOP_DB - FLOOR_DB)).clamp(0.0, 1.0)
            })
            .collect()
    }
}

impl Drop for SysMeter {
    fn drop(&mut self) {
        self.stop();
    }
}

/// Log-spaced band edges as FFT bin indices.
fn band_edges(nband: usize, rate: f64) -> Vec<usize> {
    let k = (FMAX / FMIN).ln() / nband as f64;
    let hz_per_bin = rate / FFT as f64;
    let mut edges: Vec<usize> = (0..=nband)
        .map(|i| ((FMIN * (k * i as f64).exp()) / hz_per_bin) as usize)
        .collect();
    // Every band must own at least one bin, or the bottom ones read zero.
    for i in 1..edges.len() {
        if edges[i] <= edges[i - 1] {
            edges[i] = edges[i - 1] + 1;
        }
    }
    edges
}

/// In-place iterative radix-2 complex FFT. `re`/`im` must be `FFT` long.
fn fft(re: &mut [f64], im: &mut [f64]) {
    let n = re.len();
    // Bit-reversal permutation.
    let mut j = 0usize;
    for i in 1..n {
        let mut bit = n >> 1;
        while j & bit != 0 {
            j ^= bit;
            bit >>= 1;
        }
        j |= bit;
        if i < j {
            re.swap(i, j);
            im.swap(i, j);
        }
    }
    let mut len = 2usize;
    while len <= n {
        let ang = -2.0 * std::f64::consts::PI / len as f64;
        let (wr, wi) = (ang.cos(), ang.sin());
        let mut i = 0;
        while i < n {
            let (mut cr, mut ci) = (1.0f64, 0.0f64);
            for k in 0..len / 2 {
                let (ur, ui) = (re[i + k], im[i + k]);
                let (vr, vi) = (
                    re[i + k + len / 2] * cr - im[i + k + len / 2] * ci,
                    re[i + k + len / 2] * ci + im[i + k + len / 2] * cr,
                );
                re[i + k] = ur + vr;
                im[i + k] = ui + vi;
                re[i + k + len / 2] = ur - vr;
                im[i + k + len / 2] = ui - vi;
                let nr = cr * wr - ci * wi;
                ci = cr * wi + ci * wr;
                cr = nr;
            }
            i += len;
        }
        len <<= 1;
    }
}

/// Capture forever into a rolling buffer. Its own thread, so a stalled audio
/// device can never hold up the serial loop.
fn capture_loop(
    tx: mpsc::Sender<std::result::Result<(u32, String), String>>,
    buf: Arc<Mutex<Vec<f32>>>,
    run: Arc<AtomicBool>,
    _rate: Arc<AtomicU32>,
) {
    unsafe {
        let _ = CoInitializeEx(None, COINIT_MULTITHREADED);
        let started = (|| -> windows::core::Result<(IAudioClient, IAudioCaptureClient, u32, u16, u16, String)> {
            let enumerator: IMMDeviceEnumerator =
                CoCreateInstance(&MMDeviceEnumerator, None, CLSCTX_ALL)?;
            let dev = enumerator.GetDefaultAudioEndpoint(eRender, eConsole)?;
            let client: IAudioClient = dev.Activate(CLSCTX_ALL, None)?;
            let pwfx = client.GetMixFormat()?;
            let (rate, ch, bits) = ((*pwfx).nSamplesPerSec, (*pwfx).nChannels, (*pwfx).wBitsPerSample);
            // A render endpoint opened with the LOOPBACK flag captures the mix
            // it is playing. 200 ms of buffer is far more than the poll needs
            // and costs nothing.
            let r = client.Initialize(
                AUDCLNT_SHAREMODE_SHARED,
                AUDCLNT_STREAMFLAGS_LOOPBACK,
                2_000_000,
                0,
                pwfx,
                None,
            );
            CoTaskMemFree(Some(pwfx as *const _));
            r?;
            let capture: IAudioCaptureClient = client.GetService()?;
            client.Start()?;
            let name = crate::audio::default_endpoint_name().unwrap_or_else(|| "output".into());
            Ok((client, capture, rate, ch, bits, name))
        })();

        let (client, capture, rate, ch, bits, name) = match started {
            Ok(v) => v,
            Err(e) => {
                let _ = tx.send(Err(format!("no loopback device: {e}")));
                return;
            }
        };
        let _ = tx.send(Ok((rate, name)));

        while run.load(Ordering::SeqCst) {
            let mut packet = capture.GetNextPacketSize().unwrap_or(0);
            if packet == 0 {
                // Nothing rendered yet. Sleeping a fraction of the FFT window
                // keeps this thread near-idle while silence is playing.
                std::thread::sleep(Duration::from_millis(5));
                continue;
            }
            while packet > 0 && run.load(Ordering::SeqCst) {
                let mut pdata = std::ptr::null_mut();
                let mut frames = 0u32;
                let mut flags = 0u32;
                if capture
                    .GetBuffer(&mut pdata, &mut frames, &mut flags, None, None)
                    .is_err()
                {
                    break;
                }
                if frames > 0 {
                    let n = frames as usize;
                    // A silent packet carries no data worth reading, but the
                    // buffer must still advance or the meter freezes on the
                    // last thing that played.
                    let mono: Vec<f32> = if flags & AUDCLNT_BUFFERFLAGS_SILENT.0 as u32 != 0 {
                        vec![0.0; n]
                    } else {
                        downmix(pdata, n, ch as usize, bits)
                    };
                    push(&buf, &mono);
                }
                let _ = capture.ReleaseBuffer(frames);
                packet = capture.GetNextPacketSize().unwrap_or(0);
            }
        }
        let _ = client.Stop();
    }
}

/// Interleaved endpoint samples -> mono f32 in -1..1.
///
/// The shared-mode mix format is 32-bit float on every machine this has been
/// run on; 16-bit PCM is handled because a WAVEFORMATEXTENSIBLE can still say
/// so and reading it as float would be noise rather than audio.
unsafe fn downmix(p: *const u8, frames: usize, ch: usize, bits: u16) -> Vec<f32> {
    let ch = ch.max(1);
    let mut out = Vec::with_capacity(frames);
    match bits {
        32 => {
            let s = std::slice::from_raw_parts(p as *const f32, frames * ch);
            for f in 0..frames {
                let mut acc = 0.0f32;
                for c in 0..ch {
                    acc += s[f * ch + c];
                }
                out.push(acc / ch as f32);
            }
        }
        16 => {
            let s = std::slice::from_raw_parts(p as *const i16, frames * ch);
            for f in 0..frames {
                let mut acc = 0.0f32;
                for c in 0..ch {
                    acc += s[f * ch + c] as f32 / 32768.0;
                }
                out.push(acc / ch as f32);
            }
        }
        _ => out.resize(frames, 0.0),
    }
    out
}

/// Slide `x` into the tail of the rolling buffer.
fn push(buf: &Arc<Mutex<Vec<f32>>>, x: &[f32]) {
    if let Ok(mut b) = buf.lock() {
        let n = x.len();
        if n >= FFT {
            b.copy_from_slice(&x[n - FFT..]);
        } else {
            b.copy_within(n.., 0);
            b[FFT - n..].copy_from_slice(x);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn every_band_owns_a_bin() {
        let e = band_edges(21, 48000.0);
        assert_eq!(e.len(), 22);
        for i in 1..e.len() {
            assert!(e[i] > e[i - 1], "band {i} owns no bin");
        }
    }

    #[test]
    fn fft_finds_a_tone_where_it_was_put() {
        // A 1 kHz sine at 48 kHz lands in bin 1000 / (48000/2048) = 42.67,
        // i.e. bins 42-43 once the window spreads it.
        let mut re: Vec<f64> = (0..FFT)
            .map(|i| (2.0 * std::f64::consts::PI * 1000.0 * i as f64 / 48000.0).sin())
            .collect();
        let mut im = vec![0.0; FFT];
        fft(&mut re, &mut im);
        let mag: Vec<f64> = (0..FFT / 2)
            .map(|i| (re[i] * re[i] + im[i] * im[i]).sqrt())
            .collect();
        let peak = mag
            .iter()
            .enumerate()
            .max_by(|a, b| a.1.partial_cmp(b.1).unwrap())
            .map(|(i, _)| i)
            .unwrap();
        assert!((42..=43).contains(&peak), "1 kHz landed in bin {peak}");
    }

    #[test]
    fn dc_is_bin_zero() {
        let mut re = vec![1.0f64; FFT];
        let mut im = vec![0.0f64; FFT];
        fft(&mut re, &mut im);
        assert!((re[0] - FFT as f64).abs() < 1e-6);
        assert!(re[1].abs() < 1e-6);
    }

    #[test]
    fn rolling_buffer_keeps_the_newest_samples() {
        let buf = Arc::new(Mutex::new(vec![0.0f32; FFT]));
        push(&buf, &[1.0, 2.0, 3.0]);
        let b = buf.lock().unwrap();
        assert_eq!(&b[FFT - 3..], &[1.0, 2.0, 3.0]);
        assert_eq!(b[FFT - 4], 0.0);
    }

    #[test]
    fn an_oversized_packet_keeps_only_its_tail() {
        let buf = Arc::new(Mutex::new(vec![0.0f32; FFT]));
        let big: Vec<f32> = (0..FFT + 10).map(|i| i as f32).collect();
        push(&buf, &big);
        let b = buf.lock().unwrap();
        assert_eq!(b[FFT - 1], (FFT + 9) as f32);
    }
}
