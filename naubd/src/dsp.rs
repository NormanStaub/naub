//! The sample-level work of the macOS engine, free of Core Audio.
//!
//! Everything here runs inside a real-time IOProc on the Mac: no allocation, no
//! locks, no logging, and nothing that can panic on a well-formed buffer. It is
//! a separate module, compiled on every target under `cfg(test)`, so the part
//! of the port that decides what you hear can be tested on any machine.
//!
//! Filters are the same RBJ cookbook biquads as `device/eqcurve.coeffs`, which
//! is also what Equalizer APO implements, so the curve the panel draws is the
//! curve that gets applied.

#![cfg_attr(not(target_os = "macos"), allow(dead_code))]

use std::sync::atomic::{AtomicU32, AtomicUsize, Ordering};

pub const NBAND: usize = 3;
/// Smoothing on a knob move, so a detent is not a click.
pub const RAMP_S: f32 = 0.030;
/// How fast a held peak falls between reads. The bridge polls at its own rate
/// and more than one reader looks at the same app, so a read must not reset it.
pub const PEAK_TAU_S: f32 = 0.060;
/// A band closer to flat than this is not worth running audio through us for.
const FLAT_DB: f32 = 0.01;

#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub struct Coeffs {
    pub b0: f64,
    pub b1: f64,
    pub b2: f64,
    pub a1: f64,
    pub a2: f64,
}

pub const KIND_PK: u32 = 0;
pub const KIND_LSC: u32 = 1;
pub const KIND_HSC: u32 = 2;

pub fn kind_code(kind: &str) -> u32 {
    match kind {
        "LSC" => KIND_LSC,
        "HSC" => KIND_HSC,
        _ => KIND_PK,
    }
}

/// `device/eqcurve.coeffs`, line for line, its Nyquist clamp included.
#[allow(clippy::approx_constant)]
pub fn coeffs(kind: u32, f0: f64, db: f64, q: f64, rate: f64) -> Coeffs {
    let a = 10f64.powf(db / 40.0);
    let w0 = (2.0 * std::f64::consts::PI * f0 / rate).min(3.14159);
    let (cw, sw) = (w0.cos(), w0.sin());
    let alpha = sw / (2.0 * q.max(0.01));
    let (b0, b1, b2, a0, a1, a2);
    if kind == KIND_PK {
        b0 = 1.0 + alpha * a;
        b1 = -2.0 * cw;
        b2 = 1.0 - alpha * a;
        a0 = 1.0 + alpha / a;
        a1 = -2.0 * cw;
        a2 = 1.0 - alpha / a;
    } else {
        let sa = 2.0 * a.sqrt() * alpha;
        let (ap1, am1) = (a + 1.0, a - 1.0);
        if kind == KIND_LSC {
            b0 = a * (ap1 - am1 * cw + sa);
            b1 = 2.0 * a * (am1 - ap1 * cw);
            b2 = a * (ap1 - am1 * cw - sa);
            a0 = ap1 + am1 * cw + sa;
            a1 = -2.0 * (am1 + ap1 * cw);
            a2 = ap1 + am1 * cw - sa;
        } else {
            b0 = a * (ap1 + am1 * cw + sa);
            b1 = -2.0 * a * (am1 + ap1 * cw);
            b2 = a * (ap1 + am1 * cw - sa);
            a0 = ap1 - am1 * cw + sa;
            a1 = 2.0 * (am1 - ap1 * cw);
            a2 = ap1 - am1 * cw - sa;
        }
    }
    Coeffs { b0: b0 / a0, b1: b1 / a0, b2: b2 / a0, a1: a1 / a0, a2: a2 / a0 }
}

/// One band, stereo, transposed direct form II. A pass-through until it is
/// given coefficients.
#[derive(Clone, Copy, Debug)]
pub struct Biquad {
    c: Coeffs,
    z1: [f64; 2],
    z2: [f64; 2],
    on: bool,
}

impl Default for Biquad {
    fn default() -> Self {
        Self { c: Coeffs::default(), z1: [0.0; 2], z2: [0.0; 2], on: false }
    }
}

impl Biquad {
    /// New coefficients keep the state: a knob sweep should not click.
    pub fn set(&mut self, c: Option<Coeffs>) {
        match c {
            Some(c) => {
                self.c = c;
                self.on = true;
            }
            None => {
                self.on = false;
                self.z1 = [0.0; 2];
                self.z2 = [0.0; 2];
            }
        }
    }

    #[inline]
    pub fn run(&mut self, ch: usize, x: f32) -> f32 {
        if !self.on {
            return x;
        }
        let x = x as f64;
        let y = self.c.b0 * x + self.z1[ch];
        self.z1[ch] = self.c.b1 * x - self.c.a1 * y + self.z2[ch];
        self.z2[ch] = self.c.b2 * x - self.c.a2 * y;
        y as f32
    }
}

/// `(kind, on, f0, db, q)`.
pub type BandParams = (u32, bool, f32, f32, f32);

/// The panel's EQ, shared with every IOProc. Published under a sequence
/// counter (odd while writing) so a callback can never run half of one curve
/// and half of the next; it simply keeps the old one for another block.
pub struct EqParams {
    seq: AtomicU32,
    /// kind, on, f0, db, q per band; floats by bit pattern.
    bands: [[AtomicU32; 5]; NBAND],
    active: AtomicU32,
}

#[allow(clippy::declare_interior_mutable_const)]
const ZERO: AtomicU32 = AtomicU32::new(0);
#[allow(clippy::declare_interior_mutable_const)]
const ZEROS: [AtomicU32; 5] = [ZERO; 5];

pub static EQ: EqParams = EqParams::new();

impl EqParams {
    pub const fn new() -> Self {
        Self { seq: AtomicU32::new(0), bands: [ZEROS; NBAND], active: AtomicU32::new(0) }
    }

    /// Only ever called from one thread.
    pub fn publish(&self, bands: &[BandParams]) {
        let s = self.seq.load(Ordering::Relaxed);
        self.seq.store(s.wrapping_add(1), Ordering::Release);
        let mut active = 0;
        for (slot, b) in self.bands.iter().zip(bands) {
            slot[0].store(b.0, Ordering::Release);
            slot[1].store(b.1 as u32, Ordering::Release);
            slot[2].store(b.2.to_bits(), Ordering::Release);
            slot[3].store(b.3.to_bits(), Ordering::Release);
            slot[4].store(b.4.to_bits(), Ordering::Release);
            if b.1 && b.3.abs() > FLAT_DB {
                active = 1;
            }
        }
        self.active.store(active, Ordering::Release);
        self.seq.store(s.wrapping_add(2), Ordering::Release);
    }

    /// Is any band doing anything? If not, audio need not come through us.
    pub fn active(&self) -> bool {
        self.active.load(Ordering::Acquire) != 0
    }

    pub fn generation(&self) -> u32 {
        self.seq.load(Ordering::Acquire)
    }

    /// A consistent read, or `None` if a publish is underway. RT safe.
    pub fn snapshot(&self) -> Option<(u32, [BandParams; NBAND])> {
        let s = self.seq.load(Ordering::Acquire);
        if s & 1 != 0 {
            return None;
        }
        let mut out = [(0, false, 0.0, 0.0, 0.0); NBAND];
        for (o, b) in out.iter_mut().zip(&self.bands) {
            *o = (
                b[0].load(Ordering::Acquire),
                b[1].load(Ordering::Acquire) != 0,
                f32::from_bits(b[2].load(Ordering::Acquire)),
                f32::from_bits(b[3].load(Ordering::Acquire)),
                f32::from_bits(b[4].load(Ordering::Acquire)),
            );
        }
        if self.seq.load(Ordering::Acquire) == s {
            Some((s, out))
        } else {
            None
        }
    }
}

/// One tap's filter chain, kept current with [`EqParams`]. Owned by its IOProc:
/// each runs concurrently with the others, so state cannot be shared.
#[derive(Default)]
pub struct EqChain {
    bands: [Biquad; NBAND],
    seen: Option<u32>,
}

impl EqChain {
    #[inline]
    pub fn sync(&mut self, eq: &EqParams, rate: f64) {
        let g = eq.generation();
        if self.seen == Some(g) {
            return;
        }
        if let Some((s, params)) = eq.snapshot() {
            for (bq, p) in self.bands.iter_mut().zip(&params) {
                let flat = !p.1 || p.3.abs() <= FLAT_DB;
                bq.set(if flat {
                    None
                } else {
                    Some(coeffs(p.0, p.2 as f64, p.3 as f64, p.4 as f64, rate))
                });
            }
            self.seen = Some(s);
        }
    }

    #[inline]
    pub fn run(&mut self, ch: usize, x: f32) -> f32 {
        let mut v = x;
        for b in &mut self.bands {
            v = b.run(ch, v);
        }
        v
    }
}

/// Largest absolute sample. RT safe.
#[inline]
pub fn peak(samples: &[f32]) -> f32 {
    samples.iter().fold(0.0f32, |m, s| m.max(s.abs()))
}

/// The peak meter's fall across one block of `frames`.
#[inline]
pub fn peak_decay(frames: usize, rate: f32) -> f32 {
    (-(frames as f32) / (rate.max(1.0) * PEAK_TAU_S)).exp()
}

/// Per-frame coefficient of the gain ramp.
pub fn ramp_coef(rate: f32) -> f32 {
    1.0 - (-1.0 / (rate.max(1.0) * RAMP_S)).exp()
}

/// Where the audio goes in the output stream, and how loud.
pub struct RenderOut {
    pub channels: usize,
    /// The device's preferred stereo pair, 0-based.
    pub left: usize,
    pub right: usize,
}

/// One tap's block into one interleaved output buffer: ramped gain, then EQ.
/// Whichever of the two has fewer frames sets the length, and the rest of the
/// output is left silent. RT safe.
#[allow(clippy::too_many_arguments)]
pub fn render(
    input: &[f32],
    in_ch: usize,
    output: &mut [f32],
    out: &RenderOut,
    gain: &mut f32,
    target: f32,
    coef: f32,
    eq: &mut EqChain,
) {
    output.iter_mut().for_each(|s| *s = 0.0);
    if in_ch == 0 || out.channels == 0 {
        return;
    }
    let (l, r) = if out.channels == 1 {
        (0, 0)
    } else {
        let l = if out.left < out.channels { out.left } else { 0 };
        let r = if out.right < out.channels { out.right } else { 1 };
        (l, r)
    };
    for (src, dst) in input.chunks_exact(in_ch).zip(output.chunks_exact_mut(out.channels)) {
        *gain += (target - *gain) * coef;
        let (sl, sr) = if in_ch == 1 { (src[0], src[0]) } else { (src[0], src[1]) };
        let yl = eq.run(0, sl) * *gain;
        let yr = eq.run(1, sr) * *gain;
        if l == r {
            dst[l] = (yl + yr) * 0.5;
        } else {
            dst[l] = yl;
            dst[r] = yr;
        }
    }
}

/// Single-producer, single-consumer sample ring, for the spectrum: the IOProc
/// writes and may lap the reader, which only means an older window is lost.
pub struct Ring<const N: usize> {
    data: [AtomicU32; N],
    written: AtomicUsize,
}

impl<const N: usize> Ring<N> {
    pub const fn new() -> Self {
        Self { data: [ZERO; N], written: AtomicUsize::new(0) }
    }

    /// Interleaved in, mono out. RT safe.
    #[inline]
    pub fn push_downmix(&self, input: &[f32], ch: usize) {
        if ch == 0 {
            return;
        }
        let mut w = self.written.load(Ordering::Relaxed);
        for frame in input.chunks_exact(ch) {
            let mono = frame.iter().sum::<f32>() / ch as f32;
            self.data[w % N].store(mono.to_bits(), Ordering::Relaxed);
            w = w.wrapping_add(1);
        }
        self.written.store(w, Ordering::Release);
    }

    /// Everything since `*cursor`, oldest first, capped at what the ring holds.
    pub fn drain(&self, cursor: &mut usize, out: &mut Vec<f32>) {
        out.clear();
        let w = self.written.load(Ordering::Acquire);
        let n = w.wrapping_sub(*cursor).min(N);
        let start = w.wrapping_sub(n);
        out.extend((0..n).map(|i| {
            f32::from_bits(self.data[start.wrapping_add(i) % N].load(Ordering::Relaxed))
        }));
        *cursor = w;
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Magnitude in dB of a biquad at `f`, by evaluating H(z) on the unit circle.
    fn db_at(c: Coeffs, f: f64, rate: f64) -> f64 {
        let w = 2.0 * std::f64::consts::PI * f / rate;
        let (c1, s1, c2, s2) = (w.cos(), w.sin(), (2.0 * w).cos(), (2.0 * w).sin());
        let (nr, ni) = (c.b0 + c.b1 * c1 + c.b2 * c2, c.b1 * s1 + c.b2 * s2);
        let (dr, di) = (1.0 + c.a1 * c1 + c.a2 * c2, c.a1 * s1 + c.a2 * s2);
        10.0 * ((nr * nr + ni * ni) / (dr * dr + di * di)).log10()
    }

    #[test]
    fn a_peak_peaks_by_its_gain_at_its_frequency() {
        let c = coeffs(KIND_PK, 1000.0, 6.0, 0.9, 48000.0);
        assert!((db_at(c, 1000.0, 48000.0) - 6.0).abs() < 0.01);
        assert!(db_at(c, 50.0, 48000.0).abs() < 0.2);
    }

    #[test]
    fn shelves_shelve_the_right_end() {
        let lo = coeffs(KIND_LSC, 120.0, 9.0, 0.7, 48000.0);
        assert!((db_at(lo, 20.0, 48000.0) - 9.0).abs() < 0.5);
        assert!(db_at(lo, 10000.0, 48000.0).abs() < 0.1);
        let hi = coeffs(KIND_HSC, 4000.0, -9.0, 0.7, 48000.0);
        assert!((db_at(hi, 20000.0, 48000.0) + 9.0).abs() < 0.5);
        assert!(db_at(hi, 100.0, 48000.0).abs() < 0.1);
    }

    /// Against `device/eqcurve.coeffs` run under CPython: if these drift apart,
    /// the panel draws one curve and the Mac plays another.
    #[test]
    fn matches_the_panels_own_coefficients() {
        let cases = [
            (KIND_PK, 1000.0, 6.0, 1.0, [1.043953087, -1.8953207239, 0.8677222848, -1.8953207239, 0.9116753718]),
            (KIND_LSC, 120.0, 9.0, 0.7, [1.0058937728, -1.9825518067, 0.9770687079, -1.9826842874, 0.98283]),
            (KIND_HSC, 4000.0, -9.0, 0.7, [0.4286040226, -0.4663691362, 0.1655987104, -1.4306320284, 0.5584656252]),
        ];
        for (kind, f0, db, q, want) in cases {
            let c = coeffs(kind, f0, db, q, 48000.0);
            let got = [c.b0, c.b1, c.b2, c.a1, c.a2];
            for (g, w) in got.iter().zip(&want) {
                assert!((g - w).abs() < 1e-8, "kind {kind}: {got:?} != {want:?}");
            }
        }
    }

    #[test]
    fn a_flat_chain_passes_samples_untouched() {
        let mut eq = EqChain::default();
        assert_eq!(eq.run(0, 0.25), 0.25);
    }

    #[test]
    fn the_filter_runs_each_channel_independently() {
        let mut bq = Biquad::default();
        bq.set(Some(coeffs(KIND_PK, 1000.0, 6.0, 1.0, 48000.0)));
        let a = bq.run(0, 1.0);
        let b = bq.run(1, 1.0);
        assert_eq!(a, b, "the right channel must not see the left's state");
    }

    fn stereo_out() -> RenderOut {
        RenderOut { channels: 2, left: 0, right: 1 }
    }

    #[test]
    fn unity_gain_is_a_copy() {
        let input = [0.5, -0.5, 0.25, -0.25];
        let mut out = [9.0; 4];
        let mut g = 1.0;
        render(&input, 2, &mut out, &stereo_out(), &mut g, 1.0, 0.1, &mut EqChain::default());
        assert_eq!(out, input);
    }

    #[test]
    fn the_gain_ramps_rather_than_steps() {
        let input = [1.0f32; 2000];
        let mut out = [0.0f32; 2000];
        let mut g = 1.0;
        let coef = ramp_coef(48000.0);
        render(&input, 2, &mut out, &stereo_out(), &mut g, 0.0, coef, &mut EqChain::default());
        assert!(out[0] > 0.99, "must start where it was: {}", out[0]);
        assert!(out[0] > out[1998], "must be falling");
        assert!(g < 0.6 && g > 0.4, "1000 frames is about 21 ms of a 30 ms ramp: {g}");
    }

    #[test]
    fn stereo_lands_on_the_preferred_pair_of_a_multichannel_device() {
        let input = [0.5, -0.5];
        let mut out = [9.0; 6];
        let mut g = 1.0;
        let o = RenderOut { channels: 6, left: 2, right: 3 };
        render(&input, 2, &mut out, &o, &mut g, 1.0, 0.1, &mut EqChain::default());
        assert_eq!(out, [0.0, 0.0, 0.5, -0.5, 0.0, 0.0]);
    }

    #[test]
    fn a_mono_device_gets_both_channels() {
        let mut out = [9.0; 1];
        let mut g = 1.0;
        let o = RenderOut { channels: 1, left: 0, right: 1 };
        render(&[0.5, 0.25], 2, &mut out, &o, &mut g, 1.0, 0.1, &mut EqChain::default());
        assert_eq!(out, [0.375]);
    }

    #[test]
    fn an_absurd_preferred_pair_cannot_index_out_of_range() {
        let mut out = [9.0; 4];
        let mut g = 1.0;
        let o = RenderOut { channels: 2, left: 40, right: 41 };
        render(&[0.5, -0.5], 2, &mut out, &o, &mut g, 1.0, 0.1, &mut EqChain::default());
        assert_eq!(out, [0.5, -0.5, 0.0, 0.0]);
    }

    #[test]
    fn a_short_input_leaves_the_tail_silent() {
        let mut out = [9.0; 6];
        let mut g = 1.0;
        render(&[0.5, 0.5], 2, &mut out, &stereo_out(), &mut g, 1.0, 0.1, &mut EqChain::default());
        assert_eq!(&out[2..], &[0.0, 0.0, 0.0, 0.0]);
    }

    #[test]
    fn publishing_reaches_a_chain_and_flat_reads_inactive() {
        let eq = EqParams::new();
        assert!(!eq.active());
        eq.publish(&[(KIND_PK, true, 1000.0, 6.0, 1.0); 3]);
        assert!(eq.active());
        let mut chain = EqChain::default();
        chain.sync(&eq, 48000.0);
        assert_ne!(chain.run(0, 1.0), 1.0);
        eq.publish(&[(KIND_PK, true, 1000.0, 0.0, 1.0); 3]);
        assert!(!eq.active());
        chain.sync(&eq, 48000.0);
        assert_eq!(chain.run(0, 1.0), 1.0);
    }

    #[test]
    fn a_band_that_is_off_is_inactive_whatever_its_gain() {
        let eq = EqParams::new();
        eq.publish(&[(KIND_PK, false, 1000.0, 6.0, 1.0); 3]);
        assert!(!eq.active());
    }

    #[test]
    fn the_peak_sees_the_negative_side() {
        assert_eq!(peak(&[0.1, -0.7, 0.2]), 0.7);
        assert_eq!(peak(&[]), 0.0);
    }

    #[test]
    fn the_ring_returns_what_went_in_and_survives_being_lapped() {
        let r: Ring<8> = Ring::new();
        let mut cursor = 0;
        let mut out = Vec::new();
        r.push_downmix(&[1.0, 3.0, 5.0, 7.0], 2);
        r.drain(&mut cursor, &mut out);
        assert_eq!(out, [2.0, 6.0]);
        r.drain(&mut cursor, &mut out);
        assert!(out.is_empty());
        let many: Vec<f32> = (0..20).map(|i| i as f32).collect();
        r.push_downmix(&many, 1);
        r.drain(&mut cursor, &mut out);
        assert_eq!(out.len(), 8);
        assert_eq!(out[7], 19.0);
    }
}
