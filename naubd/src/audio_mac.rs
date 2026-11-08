//! macOS per-app + system audio: Core Audio process taps (macOS 14.4+; target 26+).
//!
//! The same surface `audio.rs` gives the bridge on Windows, over a platform
//! with neither of the things that file leans on: there is no per-app peak and
//! no per-app volume API (`firmware/v2/MACOS_PORT.md` §3.1-3.2). So this is a
//! small engine, and four decisions shape it:
//!
//! * **One tap, one private aggregate and one IOProc per app.** A running
//!   aggregate's tap list cannot be changed, so a shared one would mean every
//!   app glitching whenever any app started. FineTune is built the same way.
//! * **An app at 100% with a flat EQ is left alone.** Its tap is unmuted and only
//!   metered; what you hear is still the app going straight to the device, with
//!   no added latency. Only moving a knob (or the EQ) puts an app through the
//!   re-render path: muted at the source, gain-scaled copy written out here.
//! * **Nothing is muted until a sample has been seen.** A denied or missing
//!   audio-capture grant does not fail, it delivers silence -- and muting a
//!   source we then cannot hear would make that app go quiet with no error
//!   anywhere. Until the first non-zero sample ([`capture_ok`]) knobs are stored
//!   but not applied, and the panel's EQ reads INERT.
//! * **All HAL calls live on one worker thread.** They block, sometimes for
//!   hundreds of ms on Bluetooth. The bridge's serial loop only ever takes a
//!   short mutex and reads atomics.
//!
//! With nothing playing there are no taps and no IOProcs at all, so idle cost
//! is one thread polling the process list once a second.
//!
//! **A muted tap must not outlive us.** After a crash, an orphaned aggregate
//! holding a `MutedWhenTapped` tap can leave an app silently muted with no
//! program left to blame (FineTune hit this; `isPrivate` is no promise). So
//! fatal signals tear down what is live ([`guard_signals`]), and startup sweeps
//! whatever a `kill -9` left ([`sweep_orphans`]).

use std::collections::{BTreeMap, HashMap, HashSet};
use std::ffi::{c_void, CStr};
use std::mem::{size_of, MaybeUninit};
use std::path::PathBuf;
use std::ptr::{addr_of, addr_of_mut, NonNull};
use std::sync::atomic::{AtomicBool, AtomicU32, AtomicU64, Ordering};
use std::sync::{mpsc, Arc, Condvar, Mutex, MutexGuard, Once};
use std::time::{Duration, Instant};

use objc2::rc::{autoreleasepool, Retained};
use objc2::runtime::AnyObject;
use objc2::{AnyThread, Message};
use objc2_core_audio::*;
use objc2_core_audio_types::{AudioBuffer, AudioBufferList, AudioTimeStamp};
use objc2_core_foundation::{CFDictionary, CFRetained, CFString};
use objc2_foundation::{
    NSActivityOptions, NSArray, NSDictionary, NSNumber, NSProcessInfo, NSString, NSUUID,
};
use serde::{Deserialize, Serialize};

use crate::appkey;
use crate::dsp::{self, EqChain, RenderOut, Ring};

pub const SYSTEM_CH: u8 = 3;
pub const CURVE: f32 = 0.45;

const SYSTEM: AudioObjectID = kAudioObjectSystemObject as AudioObjectID;
const SCAN: Duration = Duration::from_secs(1);
/// A listener can fire in bursts; there is no need to scan at that rate.
const SCAN_MIN: Duration = Duration::from_millis(100);
/// How long a meter-only tap outlives the app's last sound. Pausing a track and
/// starting it again should not cost a rebuild.
const LINGER: Duration = Duration::from_secs(15);
/// A new helper process joining an app rebuilds its tap, but not repeatedly.
const PROC_CHANGE_MIN: Duration = Duration::from_secs(2);
const RETRY: Duration = Duration::from_secs(30);
const STALL: Duration = Duration::from_secs(5);
const GRANT_WARN: Duration = Duration::from_secs(15);
/// The all-zeros bug (forum 825780): the IOProc keeps firing, every sample is
/// 0, and only a teardown cures it. A silent app looks identical, so back off.
const ZERO_AFTER: Duration = Duration::from_secs(30);
const ZERO_BACKOFF_MIN: Duration = Duration::from_secs(60);
const ZERO_BACKOFF_MAX: Duration = Duration::from_secs(600);
const UNITY: f32 = 0.999;
/// Every aggregate we make is named this, which is how a later run finds one
/// that an earlier run could not clean up.
const AGG_PREFIX: &str = "naub-";

static CAPTURE_OK: AtomicBool = AtomicBool::new(false);
/// Roughly 170 ms at 48 kHz; the spectrum wants 43 ms of it at a time.
static SPECTRUM: Ring<8192> = Ring::new();

/// Has any tap ever delivered a non-zero sample? There is no API to ask about
/// the grant itself; a sample is the only proof, here as everywhere.
pub fn capture_ok() -> bool {
    CAPTURE_OK.load(Ordering::Acquire)
}

// ---- HAL property helpers -----------------------------------------------------

fn addr(sel: u32, scope: u32, element: u32) -> AudioObjectPropertyAddress {
    AudioObjectPropertyAddress { mSelector: sel, mScope: scope, mElement: element }
}

fn get<T: Copy>(obj: AudioObjectID, sel: u32, scope: u32, element: u32) -> Option<T> {
    let a = addr(sel, scope, element);
    let mut v = MaybeUninit::<T>::uninit();
    let mut size = size_of::<T>() as u32;
    let st = unsafe {
        AudioObjectGetPropertyData(
            obj,
            NonNull::from(&a),
            0,
            std::ptr::null(),
            NonNull::from(&mut size),
            NonNull::new_unchecked(v.as_mut_ptr() as *mut c_void),
        )
    };
    if st == 0 && size as usize == size_of::<T>() {
        // SAFETY: the HAL wrote exactly size_of::<T>() bytes, and every T used
        // here is a plain integer, float or pair of them.
        Some(unsafe { v.assume_init() })
    } else {
        None
    }
}

fn set<T: Copy>(obj: AudioObjectID, sel: u32, scope: u32, element: u32, v: T) -> bool {
    let a = addr(sel, scope, element);
    let st = unsafe {
        AudioObjectSetPropertyData(
            obj,
            NonNull::from(&a),
            0,
            std::ptr::null(),
            size_of::<T>() as u32,
            NonNull::from(&v).cast(),
        )
    };
    st == 0
}

fn has(obj: AudioObjectID, sel: u32, scope: u32, element: u32) -> bool {
    let a = addr(sel, scope, element);
    unsafe { AudioObjectHasProperty(obj, NonNull::from(&a)) }
}

fn settable(obj: AudioObjectID, sel: u32, scope: u32, element: u32) -> bool {
    let a = addr(sel, scope, element);
    let mut out = 0u8;
    let st = unsafe { AudioObjectIsPropertySettable(obj, NonNull::from(&a), NonNull::from(&mut out)) };
    st == 0 && out != 0
}

fn get_string(obj: AudioObjectID, sel: u32, scope: u32) -> Option<String> {
    let raw: *const CFString =
        get::<usize>(obj, sel, scope, kAudioObjectPropertyElementMain).map(|p| p as *const CFString)?;
    // SAFETY: CFString properties hand back a +1 reference, which this now owns.
    let s = unsafe { CFRetained::from_raw(NonNull::new(raw as *mut CFString)?) };
    Some(s.to_string())
}

fn get_ids(obj: AudioObjectID, sel: u32, scope: u32) -> Vec<AudioObjectID> {
    let a = addr(sel, scope, kAudioObjectPropertyElementMain);
    let mut size = 0u32;
    let st = unsafe {
        AudioObjectGetPropertyDataSize(obj, NonNull::from(&a), 0, std::ptr::null(), NonNull::from(&mut size))
    };
    if st != 0 || size == 0 {
        return Vec::new();
    }
    let mut ids = vec![0 as AudioObjectID; size as usize / size_of::<AudioObjectID>()];
    let st = unsafe {
        AudioObjectGetPropertyData(
            obj,
            NonNull::from(&a),
            0,
            std::ptr::null(),
            NonNull::from(&mut size),
            NonNull::new_unchecked(ids.as_mut_ptr() as *mut c_void),
        )
    };
    if st != 0 {
        return Vec::new();
    }
    ids.truncate(size as usize / size_of::<AudioObjectID>());
    ids
}

/// An OSStatus as people see it quoted: most are four-character codes.
fn fourcc(st: i32) -> String {
    let b = st.to_be_bytes();
    if b.iter().all(|c| c.is_ascii_graphic() || *c == b' ') {
        format!("'{}'", String::from_utf8_lossy(&b))
    } else {
        st.to_string()
    }
}

fn nsstr(s: &CStr) -> Retained<NSString> {
    NSString::from_str(s.to_str().unwrap_or(""))
}

fn obj<T: Message>(x: &T) -> &AnyObject {
    // SAFETY: every Objective-C object is an AnyObject.
    unsafe { &*(x as *const T as *const AnyObject) }
}

// ---- the output device ------------------------------------------------------

#[derive(Clone, Debug, PartialEq)]
pub struct Output {
    id: AudioObjectID,
    uid: String,
    name: String,
    rate: f64,
    /// 0-based, from the device's own preference.
    left: usize,
    right: usize,
    bluetooth: bool,
    streams: usize,
}

fn default_output() -> Option<Output> {
    let global = kAudioObjectPropertyScopeGlobal;
    let main = kAudioObjectPropertyElementMain;
    let out = kAudioObjectPropertyScopeOutput;
    let id: AudioObjectID = get(SYSTEM, kAudioHardwarePropertyDefaultOutputDevice, global, main)?;
    if id == 0 {
        return None;
    }
    let uid = get_string(id, kAudioDevicePropertyDeviceUID, global)?;
    let name = get_string(id, kAudioObjectPropertyName, global).unwrap_or_else(|| uid.clone());
    let rate = get::<f64>(id, kAudioDevicePropertyNominalSampleRate, global, main).unwrap_or(48000.0);
    let pair = get::<[u32; 2]>(id, kAudioDevicePropertyPreferredChannelsForStereo, out, main)
        .unwrap_or([1, 2]);
    let transport = get::<u32>(id, kAudioDevicePropertyTransportType, global, main).unwrap_or(0);
    Some(Output {
        id,
        uid,
        name,
        rate,
        left: pair[0].saturating_sub(1) as usize,
        right: pair[1].saturating_sub(1) as usize,
        bluetooth: transport == kAudioDeviceTransportTypeBluetooth
            || transport == kAudioDeviceTransportTypeBluetoothLE,
        streams: get_ids(id, kAudioDevicePropertyStreams, out).len(),
    })
}

// ---- the real-time side -------------------------------------------------------

/// What an app's IOProc shares with everyone else. Atomics only.
struct Slot {
    /// Held peak, f32 by bit pattern. Falls by itself; reads do not reset it.
    peak: AtomicU32,
    /// Where the gain ramp is heading, f32 by bit pattern.
    target: AtomicU32,
    /// A non-zero sample since this was last cleared.
    heard: AtomicBool,
    /// IO cycles, for the stall watchdog.
    cycles: AtomicU64,
}

impl Slot {
    fn new(gain: f32) -> Self {
        Self {
            peak: AtomicU32::new(0),
            target: AtomicU32::new(gain.to_bits()),
            heard: AtomicBool::new(false),
            cycles: AtomicU64::new(0),
        }
    }

    fn peak(&self) -> f32 {
        f32::from_bits(self.peak.load(Ordering::Relaxed))
    }
}

/// Owned by one IOProc for its whole life; the Box behind it is freed only
/// after `AudioDeviceDestroyIOProcID`, which waits for the callback to finish.
struct RtCtx {
    slot: Arc<Slot>,
    mix: bool,
    spectrum: bool,
    rate: f32,
    coef: f32,
    gain: f32,
    out: RenderOut,
    eq: EqChain,
}

/// The real-time callback: no allocation, no locks, no logging, no ObjC.
///
/// The tap is the LAST input buffer. Anything before it is the output device's
/// own input streams, disabled in [`disable_device_inputs`] so that using a
/// headset as an output is not also a microphone grab.
unsafe extern "C-unwind" fn ioproc(
    _device: AudioObjectID,
    _now: NonNull<AudioTimeStamp>,
    input: NonNull<AudioBufferList>,
    _in_time: NonNull<AudioTimeStamp>,
    output: NonNull<AudioBufferList>,
    _out_time: NonNull<AudioTimeStamp>,
    client: *mut c_void,
) -> i32 {
    let ctx = &mut *(client as *mut RtCtx);
    ctx.slot.cycles.fetch_add(1, Ordering::Relaxed);

    let nin = (*input.as_ptr()).mNumberBuffers as usize;
    let ins = std::slice::from_raw_parts(addr_of!((*input.as_ptr()).mBuffers) as *const AudioBuffer, nin);
    let (mut tap, mut ch): (&[f32], usize) = (&[], 2);
    if let Some(b) = ins.last() {
        if !b.mData.is_null() && b.mNumberChannels > 0 {
            ch = b.mNumberChannels as usize;
            tap = std::slice::from_raw_parts(b.mData as *const f32, b.mDataByteSize as usize / 4);
        }
    }

    let p = dsp::peak(tap);
    if p > 0.0 {
        ctx.slot.heard.store(true, Ordering::Relaxed);
    }
    let fallen = ctx.slot.peak() * dsp::peak_decay(tap.len() / ch.max(1), ctx.rate);
    ctx.slot.peak.store(p.max(fallen).to_bits(), Ordering::Relaxed);

    if ctx.spectrum {
        SPECTRUM.push_downmix(tap, ch);
    }

    let nout = (*output.as_ptr()).mNumberBuffers as usize;
    let outs = std::slice::from_raw_parts_mut(
        addr_of_mut!((*output.as_ptr()).mBuffers) as *mut AudioBuffer,
        nout,
    );
    for (i, b) in outs.iter_mut().enumerate() {
        if b.mData.is_null() {
            continue;
        }
        let buf = std::slice::from_raw_parts_mut(b.mData as *mut f32, b.mDataByteSize as usize / 4);
        if ctx.mix && i == 0 {
            let target = f32::from_bits(ctx.slot.target.load(Ordering::Relaxed));
            ctx.eq.sync(&dsp::EQ, ctx.rate as f64);
            ctx.out.channels = b.mNumberChannels as usize;
            dsp::render(tap, ch, buf, &ctx.out, &mut ctx.gain, target, ctx.coef, &mut ctx.eq);
        } else {
            buf.iter_mut().for_each(|s| *s = 0.0);
        }
    }
    0
}

/// Mark every input stream but the last (the tap) unused. Without this, an
/// output that also has a microphone runs duplex, and macOS treats that as
/// microphone use. Failure is not fatal; the worst case is a prompt.
fn disable_device_inputs(device: AudioObjectID, proc_id: AudioDeviceIOProcID) {
    let scope = kAudioObjectPropertyScopeInput;
    let n = get_ids(device, kAudioDevicePropertyStreams, scope).len();
    if n <= 1 {
        return;
    }
    let Some(f) = proc_id else { return };
    // AudioHardwareIOProcStreamUsage is { void*; UInt32 n; UInt32 flags[n] },
    // variable length, so it is laid out by hand. u64 for the alignment.
    let bytes = size_of::<*const c_void>() + 4 + 4 * n;
    let mut raw = vec![0u64; bytes.div_ceil(8)];
    let base = raw.as_mut_ptr() as *mut u8;
    unsafe {
        (base as *mut *const c_void).write(f as *const c_void);
        let at = base.add(size_of::<*const c_void>()) as *mut u32;
        at.write(n as u32);
        at.add(n).write(1); // flags[n - 1]: the tap
    }
    let a = addr(kAudioDevicePropertyIOProcStreamUsage, scope, kAudioObjectPropertyElementMain);
    let st = unsafe {
        AudioObjectSetPropertyData(
            device,
            NonNull::from(&a),
            0,
            std::ptr::null(),
            bytes as u32,
            NonNull::new_unchecked(base as *mut c_void),
        )
    };
    if st != 0 {
        crate::logging::line(&format!(
            "could not disable the device's inputs ({}); macOS may ask about the microphone",
            fourcc(st)
        ));
    }
}

// ---- never leave anything muted behind --------------------------------------------

const MAX_TRACKED: usize = 128;
#[allow(clippy::declare_interior_mutable_const)]
const NO_AGG: AtomicU32 = AtomicU32::new(0);
/// Live aggregates, readable from a signal handler: no lock, no allocation.
static TRACKED: [AtomicU32; MAX_TRACKED] = [NO_AGG; MAX_TRACKED];

fn track(aggregate: AudioObjectID) {
    for t in &TRACKED {
        if t.compare_exchange(0, aggregate, Ordering::AcqRel, Ordering::Relaxed).is_ok() {
            return;
        }
    }
}

fn untrack(aggregate: AudioObjectID) {
    for t in &TRACKED {
        if t.compare_exchange(aggregate, 0, Ordering::AcqRel, Ordering::Relaxed).is_ok() {
            return;
        }
    }
}

/// Destroying an aggregate is a Mach IPC call to coreaudiod and needs nothing
/// from this process's heap, so it is usable even from a crash.
extern "C" fn on_fatal(sig: libc::c_int) {
    for t in &TRACKED {
        let id = t.swap(0, Ordering::AcqRel);
        if id != 0 {
            unsafe { AudioHardwareDestroyAggregateDevice(id) };
        }
    }
    unsafe {
        libc::signal(sig, libc::SIG_DFL);
        libc::raise(sig);
    }
}

/// Ctrl-C at a terminal counts: it is exactly how a test run ends.
fn guard_signals() {
    static ONCE: Once = Once::new();
    ONCE.call_once(|| {
        let handler = on_fatal as extern "C" fn(libc::c_int) as libc::sighandler_t;
        for sig in [
            libc::SIGINT,
            libc::SIGTERM,
            libc::SIGHUP,
            libc::SIGABRT,
            libc::SIGSEGV,
            libc::SIGBUS,
            libc::SIGILL,
            libc::SIGTRAP,
        ] {
            unsafe { libc::signal(sig, handler) };
        }
    });
}

/// Anything of ours still registered from a run that could not clean up. Only
/// before this process has made any of its own.
fn sweep_orphans() {
    let scope = kAudioObjectPropertyScopeGlobal;
    let main = kAudioObjectPropertyElementMain;
    for id in get_ids(SYSTEM, kAudioHardwarePropertyDevices, scope) {
        if get::<u32>(id, kAudioDevicePropertyTransportType, scope, main)
            != Some(kAudioDeviceTransportTypeAggregate)
        {
            continue;
        }
        let Some(name) = get_string(id, kAudioObjectPropertyName, scope) else { continue };
        if name.starts_with(AGG_PREFIX) {
            let st = unsafe { AudioHardwareDestroyAggregateDevice(id) };
            crate::logging::line(&format!("removed {name} left by an earlier run ({})", fourcc(st)));
        }
    }
}

// ---- a tap, its aggregate and its IOProc ---------------------------------------

enum Spec {
    Procs(Vec<AudioObjectID>),
    /// Everything but these.
    AllBut(Vec<AudioObjectID>),
}

struct Unit {
    procs: Vec<AudioObjectID>,
    output: String,
    rate: f64,
    mix: bool,
    slot: Arc<Slot>,
    tap: AudioObjectID,
    aggregate: AudioObjectID,
    proc_id: AudioDeviceIOProcID,
    started: bool,
    ctx: *mut RtCtx,
    created: Instant,
    last_heard: Option<Instant>,
    last_cycles: u64,
    last_cycled: Instant,
}

impl Unit {
    fn build(
        tag: &str,
        spec: Spec,
        mix: bool,
        out: &Output,
        slot: Arc<Slot>,
        spectrum: bool,
    ) -> Result<Unit, String> {
        let ctx = Box::into_raw(Box::new(RtCtx {
            slot: Arc::clone(&slot),
            mix,
            spectrum,
            rate: out.rate as f32,
            coef: dsp::ramp_coef(out.rate as f32),
            // Seeded, so the first callback does not ramp from nothing.
            gain: f32::from_bits(slot.target.load(Ordering::Relaxed)),
            out: RenderOut { channels: 2, left: out.left, right: out.right },
            eq: EqChain::default(),
        }));
        // From here on, Drop undoes whatever got done, in the right order.
        let mut u = Unit {
            procs: match &spec {
                Spec::Procs(p) => p.clone(),
                Spec::AllBut(_) => Vec::new(),
            },
            output: out.uid.clone(),
            rate: out.rate,
            mix,
            slot,
            tap: 0,
            aggregate: 0,
            proc_id: None,
            started: false,
            ctx,
            created: Instant::now(),
            last_heard: None,
            last_cycles: 0,
            last_cycled: Instant::now(),
        };

        let numbers = |ids: &[AudioObjectID]| -> Retained<NSArray<NSNumber>> {
            let n: Vec<Retained<NSNumber>> =
                ids.iter().map(|i| NSNumber::new_u32(*i)).collect();
            NSArray::from_retained_slice(&n)
        };
        let desc = unsafe {
            match &spec {
                Spec::Procs(p) => CATapDescription::initStereoMixdownOfProcesses(
                    CATapDescription::alloc(),
                    &numbers(p),
                ),
                Spec::AllBut(p) => CATapDescription::initStereoGlobalTapButExcludeProcesses(
                    CATapDescription::alloc(),
                    &numbers(p),
                ),
            }
        };
        let uuid = NSUUID::new();
        unsafe {
            desc.setUUID(&uuid);
            desc.setName(&NSString::from_str("naub"));
            desc.setPrivate(true);
            desc.setMuteBehavior(if mix {
                CATapMuteBehavior::MutedWhenTapped
            } else {
                CATapMuteBehavior::Unmuted
            });
        }
        let st = unsafe { AudioHardwareCreateProcessTap(Some(&desc), &mut u.tap) };
        // An unsatisfiable description "succeeds" with tap 0.
        if st != 0 || u.tap == 0 {
            return Err(format!("AudioHardwareCreateProcessTap: {}", fourcc(st)));
        }

        let no = NSNumber::new_bool(false);
        let yes = NSNumber::new_bool(true);
        let sub_uid = NSString::from_str(&out.uid);
        let sub = NSDictionary::<NSString, AnyObject>::from_slices(
            &[&*nsstr(kAudioSubDeviceUIDKey)],
            &[obj(&*sub_uid)],
        );
        // Bluetooth output and tap share a clock; compensating anyway produces
        // periodic insert/delete crackle. (Found the hard way by FineTune.)
        let drift = NSNumber::new_bool(!out.bluetooth);
        let tap_uid = uuid.UUIDString();
        let sub_tap = NSDictionary::<NSString, AnyObject>::from_slices(
            &[&*nsstr(kAudioSubTapUIDKey), &*nsstr(kAudioSubTapDriftCompensationKey)],
            &[obj(&*tap_uid), obj(&*drift)],
        );
        let subs = NSArray::from_slice(&[&*sub]);
        let taps = NSArray::from_slice(&[&*sub_tap]);
        let name = NSString::from_str(&format!("{AGG_PREFIX}{tag}"));
        let agg_uid = NSUUID::new().UUIDString();
        // A device with several output streams only comes through as one when
        // stacked; a single-stream device keeps its own channel layout.
        let stacked = if out.streams == 1 { &no } else { &yes };
        let d = NSDictionary::<NSString, AnyObject>::from_slices(
            &[
                &*nsstr(kAudioAggregateDeviceNameKey),
                &*nsstr(kAudioAggregateDeviceUIDKey),
                &*nsstr(kAudioAggregateDeviceMainSubDeviceKey),
                &*nsstr(kAudioAggregateDeviceIsPrivateKey),
                &*nsstr(kAudioAggregateDeviceIsStackedKey),
                &*nsstr(kAudioAggregateDeviceTapAutoStartKey),
                &*nsstr(kAudioAggregateDeviceSubDeviceListKey),
                &*nsstr(kAudioAggregateDeviceTapListKey),
            ],
            &[
                obj(&*name),
                obj(&*agg_uid),
                obj(&*sub_uid),
                obj(&*yes),
                obj(&**stacked),
                obj(&*yes),
                obj(&*subs),
                obj(&*taps),
            ],
        );
        // SAFETY: NSDictionary and CFDictionary are toll-free bridged.
        let cf = unsafe { &*(&*d as *const NSDictionary<NSString, AnyObject> as *const CFDictionary) };
        let st = unsafe { AudioHardwareCreateAggregateDevice(cf, NonNull::from(&mut u.aggregate)) };
        if st != 0 || u.aggregate == 0 {
            return Err(format!("AudioHardwareCreateAggregateDevice: {}", fourcc(st)));
        }
        track(u.aggregate);
        // Creation finishes asynchronously; a proc added too early can fail.
        let t0 = Instant::now();
        while get::<u32>(
            u.aggregate,
            kAudioDevicePropertyDeviceIsAlive,
            kAudioObjectPropertyScopeGlobal,
            kAudioObjectPropertyElementMain,
        ) != Some(1)
            && t0.elapsed() < Duration::from_secs(1)
        {
            std::thread::sleep(Duration::from_millis(10));
        }

        let st = unsafe {
            AudioDeviceCreateIOProcID(
                u.aggregate,
                Some(ioproc),
                u.ctx as *mut c_void,
                NonNull::from(&mut u.proc_id),
            )
        };
        if st != 0 || u.proc_id.is_none() {
            return Err(format!("AudioDeviceCreateIOProcID: {}", fourcc(st)));
        }
        disable_device_inputs(u.aggregate, u.proc_id);
        // This is the call that raises the system-audio prompt, the first time.
        let st = unsafe { AudioDeviceStart(u.aggregate, u.proc_id) };
        if st != 0 {
            return Err(format!("AudioDeviceStart: {}", fourcc(st)));
        }
        u.started = true;
        Ok(u)
    }

    fn mode(&self) -> &'static str {
        if self.mix {
            "render"
        } else {
            "meter"
        }
    }
}

impl Drop for Unit {
    /// Stop, proc, aggregate, tap, and only then the context. Destroying the
    /// proc waits for a callback in flight, which is what makes freeing safe.
    fn drop(&mut self) {
        unsafe {
            if self.started {
                AudioDeviceStop(self.aggregate, self.proc_id);
            }
            if self.proc_id.is_some() {
                AudioDeviceDestroyIOProcID(self.aggregate, self.proc_id);
            }
            if self.aggregate != 0 {
                untrack(self.aggregate);
                AudioHardwareDestroyAggregateDevice(self.aggregate);
            }
            if self.tap != 0 {
                AudioHardwareDestroyProcessTap(self.tap);
            }
            drop(Box::from_raw(self.ctx));
        }
    }
}

// ---- state shared with the bridge -----------------------------------------------

#[derive(Clone, Copy, Serialize, Deserialize, PartialEq, Debug)]
struct Pref {
    gain: f32,
    muted: bool,
}

impl Default for Pref {
    fn default() -> Self {
        Self { gain: 1.0, muted: false }
    }
}

impl Pref {
    fn effective(&self) -> f32 {
        if self.muted {
            0.0
        } else {
            self.gain
        }
    }

    /// Is this app being changed by us, or merely watched?
    fn touched(&self) -> bool {
        self.muted || self.gain < UNITY
    }
}

#[derive(Clone)]
struct Row {
    id: AudioObjectID,
    pid: i32,
    key: String,
    exe: String,
    running: bool,
}

#[derive(Default)]
struct State {
    prefs: HashMap<String, Pref>,
    prefs_dirty: bool,
    slots: HashMap<String, Arc<Slot>>,
    rows: Vec<Row>,
    output: Option<Output>,
    modes: HashMap<String, &'static str>,
    fail: Option<String>,
    scanned: bool,
    wake: bool,
    scan_now: bool,
}

struct Shared {
    state: Mutex<State>,
    cv: Condvar,
}

impl Shared {
    fn lock(&self) -> MutexGuard<'_, State> {
        self.state.lock().unwrap_or_else(|e| e.into_inner())
    }

    fn poke(&self, scan: bool) {
        let mut s = self.lock();
        s.wake = true;
        s.scan_now |= scan;
        self.cv.notify_one();
    }
}

fn slot_for(s: &mut State, key: &str) -> Arc<Slot> {
    let gain = s.prefs.get(key).copied().unwrap_or_default().effective();
    Arc::clone(s.slots.entry(key.to_string()).or_insert_with(|| Arc::new(Slot::new(gain))))
}

fn prefs_path() -> PathBuf {
    crate::pins::path().with_file_name("volumes.json")
}

fn load_prefs() -> HashMap<String, Pref> {
    std::fs::read_to_string(prefs_path())
        .ok()
        .and_then(|t| serde_json::from_str(&t).ok())
        .unwrap_or_default()
}

/// Knobs have nowhere else to live here: on Windows the OS remembers an app's
/// session volume, and on a Mac nothing does.
fn save_prefs(prefs: &HashMap<String, Pref>) {
    let path = prefs_path();
    if let Some(d) = path.parent() {
        let _ = std::fs::create_dir_all(d);
    }
    let touched: HashMap<&String, &Pref> = prefs.iter().filter(|(_, p)| p.touched()).collect();
    if let Ok(json) = serde_json::to_string(&touched) {
        if let Err(e) = std::fs::write(path, json) {
            crate::logging::line(&format!("could not save app volumes ({e})"));
        }
    }
}

// ---- the worker ------------------------------------------------------------------

unsafe extern "C-unwind" fn on_change(
    _obj: AudioObjectID,
    _n: u32,
    _addrs: NonNull<AudioObjectPropertyAddress>,
    client: *mut c_void,
) -> i32 {
    (*(client as *const Shared)).poke(true);
    0
}

struct Engine {
    shared: Arc<Shared>,
    units: HashMap<String, Unit>,
    /// Object id -> pid and what that pid is, so paths are read once.
    names: HashMap<(AudioObjectID, i32), Option<(String, String)>>,
    last_scan: Instant,
    last_running: HashMap<String, Instant>,
    retry_at: HashMap<String, Instant>,
    force: HashSet<String>,
    zero_backoff: HashMap<String, Duration>,
    running_since: Option<Instant>,
    warned_grant: bool,
    prefs_saved: Instant,
    own_pid: i32,
}

impl Engine {
    fn run(mut self) {
        sweep_orphans();
        let scope = kAudioObjectPropertyScopeGlobal;
        let main = kAudioObjectPropertyElementMain;
        // Notifications shorten a poll, they do not replace it: IsRunningOutput
        // reportedly never fires its listeners (forum 770348).
        let client = Arc::as_ptr(&self.shared) as *mut c_void;
        for sel in [kAudioHardwarePropertyProcessObjectList, kAudioHardwarePropertyDefaultOutputDevice] {
            let a = addr(sel, scope, main);
            // Never removed: this thread and its Arc live as long as the process.
            unsafe { AudioObjectAddPropertyListener(SYSTEM, NonNull::from(&a), Some(on_change), client) };
        }
        loop {
            autoreleasepool(|_| self.tick());
            let shared = Arc::clone(&self.shared);
            let mut s = shared.lock();
            let until = self.last_scan + SCAN;
            while !s.wake {
                let left = until.saturating_duration_since(Instant::now());
                if left.is_zero() {
                    break;
                }
                let (g, _) = shared.cv.wait_timeout(s, left).unwrap_or_else(|e| e.into_inner());
                s = g;
            }
            s.wake = false;
        }
    }

    fn tick(&mut self) {
        let due = self.last_scan.elapsed() >= SCAN;
        let asked = {
            let mut s = self.shared.lock();
            std::mem::take(&mut s.scan_now)
        };
        if due || (asked && self.last_scan.elapsed() >= SCAN_MIN) {
            self.scan();
            self.watch();
        } else if asked {
            self.shared.lock().scan_now = true;
            std::thread::sleep(SCAN_MIN);
            self.shared.lock().wake = true;
            return;
        }
        self.reconcile();
        self.persist();
    }

    fn scan(&mut self) {
        self.last_scan = Instant::now();
        let scope = kAudioObjectPropertyScopeGlobal;
        let main = kAudioObjectPropertyElementMain;
        let ids = get_ids(SYSTEM, kAudioHardwarePropertyProcessObjectList, scope);
        let mut rows = Vec::with_capacity(ids.len());
        let mut live = HashSet::with_capacity(ids.len());
        for id in ids {
            let Some(pid) = get::<i32>(id, kAudioProcessPropertyPID, scope, main) else { continue };
            if pid == self.own_pid || pid <= 0 {
                continue;
            }
            live.insert((id, pid));
            let name = self
                .names
                .entry((id, pid))
                .or_insert_with(|| exe_path(pid).and_then(|p| appkey::from_path(&p)));
            let Some((key, exe)) = name.clone() else { continue };
            let running =
                get::<u32>(id, kAudioProcessPropertyIsRunningOutput, scope, main).unwrap_or(0) != 0;
            rows.push(Row { id, pid, key, exe, running });
        }
        self.names.retain(|k, _| live.contains(k));
        let output = default_output();
        let mut s = self.shared.lock();
        s.rows = rows;
        s.output = output;
        s.scanned = true;
    }

    /// Everything that can go wrong without an error: no grant, a stalled
    /// device, and the all-zeros bug.
    fn watch(&mut self) {
        let running: HashSet<String> = {
            let s = self.shared.lock();
            s.rows.iter().filter(|r| r.running).map(|r| r.key.clone()).collect()
        };
        let now = Instant::now();
        for (key, u) in &mut self.units {
            if u.slot.heard.swap(false, Ordering::Relaxed) {
                u.last_heard = Some(now);
                self.zero_backoff.remove(key);
                if !CAPTURE_OK.swap(true, Ordering::AcqRel) {
                    crate::logging::line("audio capture confirmed: a tap delivered real samples");
                    self.shared.lock().fail = None;
                }
            }
            let c = u.slot.cycles.load(Ordering::Relaxed);
            if c != u.last_cycles {
                u.last_cycles = c;
                u.last_cycled = now;
            } else if (c > 0 || capture_ok()) && u.last_cycled.elapsed() > STALL {
                // Sleep and wake land here: the IOProc is simply gone. Not one
                // that has never run: that may be the permission prompt sitting
                // on screen, and rebuilding under it would raise it again.
                crate::logging::line(&format!("{key}: IO stalled, rebuilding its tap"));
                self.force.insert(key.clone());
                u.last_cycled = now;
            }
            let backoff = self.zero_backoff.get(key).copied().unwrap_or(ZERO_BACKOFF_MIN);
            if running.contains(key)
                && u.last_heard.is_some_and(|t| t.elapsed() > ZERO_AFTER)
                && u.created.elapsed() > backoff
            {
                crate::logging::line(&format!(
                    "{key}: playing but all zeros for {} s, rebuilding its tap",
                    ZERO_AFTER.as_secs()
                ));
                self.force.insert(key.clone());
                self.zero_backoff.insert(key.clone(), (backoff * 2).min(ZERO_BACKOFF_MAX));
            }
        }
        if running.is_empty() || self.units.is_empty() {
            self.running_since = None;
        } else if !capture_ok() {
            let since = *self.running_since.get_or_insert(now);
            if since.elapsed() > GRANT_WARN && !self.warned_grant {
                self.warned_grant = true;
                let msg = "no audio has come through a tap: grant System Audio Recording to \
                           whatever launched naubd (System Settings > Privacy & Security > \
                           Screen & System Audio Recording), then restart it";
                crate::logging::line(msg);
                self.shared.lock().fail = Some(msg.into());
            }
        }
    }

    fn reconcile(&mut self) {
        let eq = dsp::EQ.active();
        let ok = capture_ok();
        let (rows, output, prefs) = {
            let s = self.shared.lock();
            (s.rows.clone(), s.output.clone(), s.prefs.clone())
        };
        let Some(output) = output else {
            if !self.units.is_empty() {
                self.units.clear();
                self.publish_modes();
            }
            return;
        };

        let mut groups: BTreeMap<String, (Vec<AudioObjectID>, bool)> = BTreeMap::new();
        for r in &rows {
            let g = groups.entry(r.key.clone()).or_default();
            g.0.push(r.id);
            g.1 |= r.running;
        }
        let now = Instant::now();
        let mut changed = false;
        for (key, (mut ids, running)) in groups.iter().map(|(k, v)| (k, v.clone())) {
            ids.sort_unstable();
            if running {
                self.last_running.insert(key.clone(), now);
            }
            let pref = prefs.get(key).copied().unwrap_or_default();
            let keep = running
                // A turned-down app keeps its tap through a pause: without one
                // the next note would play at full volume until the next scan.
                || (ok && pref.touched())
                || self.last_running.get(key).is_some_and(|t| t.elapsed() < LINGER);
            if !keep {
                if self.units.remove(key).is_some() {
                    self.quiet(key);
                    changed = true;
                }
                continue;
            }
            let want_mix = ok && (pref.touched() || eq);
            let existing = self.units.get(key);
            let forced = self.force.remove(key);
            let stale = match existing {
                None => true,
                Some(u) => {
                    forced
                        || u.mix != want_mix
                        || u.output != output.uid
                        || u.rate != output.rate
                        || (u.procs != ids && u.created.elapsed() >= PROC_CHANGE_MIN)
                }
            };
            if !stale || self.retry_at.get(key).is_some_and(|t| *t > now) {
                continue;
            }
            let slot = slot_for(&mut self.shared.lock(), key);
            // Made before the old one is broken: a muted tap must never lapse
            // with nothing rendering it, or the app jumps to full volume.
            match Unit::build(key, Spec::Procs(ids), want_mix, &output, slot, false) {
                Ok(mut u) => {
                    let replaced = self.units.get(key);
                    u.last_heard = replaced.and_then(|o| o.last_heard);
                    crate::logging::line(&format!("{key}: {} on {}", u.mode(), output.name));
                    self.units.insert(key.clone(), u);
                    self.retry_at.remove(key);
                    changed = true;
                }
                Err(e) => {
                    crate::logging::line(&format!("{key}: no tap ({e}); retrying in {} s", RETRY.as_secs()));
                    self.retry_at.insert(key.clone(), now + RETRY);
                    // Still pointed at a device that is gone is worse than none.
                    if self.units.get(key).is_some_and(|u| u.output != output.uid) {
                        self.units.remove(key);
                        self.quiet(key);
                        changed = true;
                    }
                }
            }
        }
        // Apps that have gone altogether.
        let gone: Vec<String> =
            self.units.keys().filter(|k| !groups.contains_key(*k)).cloned().collect();
        for key in gone {
            self.units.remove(&key);
            self.quiet(&key);
            changed = true;
        }
        if changed {
            self.publish_modes();
        }
    }

    /// With no IOProc writing to it, a held peak would stay held forever.
    fn quiet(&self, key: &str) {
        if let Some(s) = self.shared.lock().slots.get(key) {
            s.peak.store(0, Ordering::Relaxed);
        }
    }

    fn publish_modes(&self) {
        let modes = self.units.iter().map(|(k, u)| (k.clone(), u.mode())).collect();
        self.shared.lock().modes = modes;
    }

    fn persist(&mut self) {
        if self.prefs_saved.elapsed() < Duration::from_secs(2) {
            return;
        }
        let prefs = {
            let mut s = self.shared.lock();
            if !std::mem::take(&mut s.prefs_dirty) {
                return;
            }
            s.prefs.clone()
        };
        save_prefs(&prefs);
        self.prefs_saved = Instant::now();
    }
}

fn exe_path(pid: i32) -> Option<String> {
    let mut buf = vec![0u8; libc::PROC_PIDPATHINFO_MAXSIZE as usize];
    let n = unsafe { libc::proc_pidpath(pid, buf.as_mut_ptr() as *mut c_void, buf.len() as u32) };
    if n <= 0 {
        return None;
    }
    buf.truncate(n as usize);
    String::from_utf8(buf).ok()
}

extern "C" {
    fn pthread_set_qos_class_self_np(qos: u32, relative_priority: i32) -> i32;
}
const QOS_CLASS_USER_INITIATED: u32 = 0x19;

/// Without this the daemon, which has no window, is exactly what App Nap is
/// for: timers stop being timely and the knobs go soft. The token has to stay
/// alive, so it is kept for good.
fn stay_awake() {
    static ONCE: Once = Once::new();
    ONCE.call_once(|| {
        let reason = NSString::from_str("naub is a realtime control surface");
        let token = NSProcessInfo::processInfo().beginActivityWithOptions_reason(
            NSActivityOptions::UserInitiatedAllowingIdleSystemSleep,
            &reason,
        );
        std::mem::forget(token);
    });
}

// ---- what the bridge sees -----------------------------------------------------------

pub struct Audio {
    shared: Arc<Shared>,
    by_key: HashMap<String, Vec<Row>>,
    seen: Vec<String>,
    output: Option<Output>,
    fail: Option<String>,
}

impl Audio {
    pub fn new() -> Result<Self, String> {
        stay_awake();
        guard_signals();
        // E-cores are where background work goes unless told otherwise, and
        // this thread is what a knob is waiting for.
        unsafe { pthread_set_qos_class_self_np(QOS_CLASS_USER_INITIATED, 0) };
        let shared = Arc::new(Shared {
            state: Mutex::new(State { prefs: load_prefs(), ..Default::default() }),
            cv: Condvar::new(),
        });
        let worker = Arc::clone(&shared);
        std::thread::Builder::new()
            .name("naub-audio".into())
            .spawn(move || {
                unsafe { pthread_set_qos_class_self_np(QOS_CLASS_USER_INITIATED, 0) };
                let past = Instant::now();
                Engine {
                    shared: worker,
                    units: HashMap::new(),
                    names: HashMap::new(),
                    last_scan: past - SCAN * 2,
                    last_running: HashMap::new(),
                    retry_at: HashMap::new(),
                    force: HashSet::new(),
                    zero_backoff: HashMap::new(),
                    running_since: None,
                    warned_grant: false,
                    prefs_saved: past,
                    own_pid: std::process::id() as i32,
                }
                .run()
            })
            .map_err(|e| e.to_string())?;
        let t0 = Instant::now();
        while !shared.lock().scanned && t0.elapsed() < Duration::from_secs(2) {
            std::thread::sleep(Duration::from_millis(10));
        }
        let mut a = Self { shared, by_key: HashMap::new(), seen: Vec::new(), output: None, fail: None };
        a.refresh();
        Ok(a)
    }

    pub fn default_guid(&self) -> Option<&str> {
        self.output.as_ref().map(|o| o.uid.as_str())
    }

    pub fn failure(&self) -> Option<&str> {
        self.fail.as_deref()
    }

    /// Take the worker's latest view. Never touches the HAL, so it cannot stall.
    pub fn refresh(&mut self) -> bool {
        let (rows, output, fail) = {
            let s = self.shared.lock();
            (s.rows.clone(), s.output.clone(), s.fail.clone())
        };
        self.by_key.clear();
        for r in rows {
            if r.running && !self.seen.contains(&r.key) {
                self.seen.push(r.key.clone());
            }
            self.by_key.entry(r.key.clone()).or_default().push(r);
        }
        self.output = output;
        self.fail = fail;
        true
    }

    pub fn exe_of(&self, key: &str) -> Option<String> {
        Some(self.by_key.get(key)?.first()?.exe.clone())
    }

    /// Apps producing output right now, longest-established first.
    pub fn active(&self) -> Vec<String> {
        let live = |k: &String| {
            self.by_key.get(k).is_some_and(|g| g.iter().any(|r| r.running))
        };
        let mut out: Vec<String> = self.seen.iter().filter(|k| live(k)).cloned().collect();
        let mut rest: Vec<String> =
            self.by_key.keys().filter(|k| live(k) && !out.contains(k)).cloned().collect();
        rest.sort();
        out.extend(rest);
        out
    }

    /// As on Windows, minus its last rung ("anything else holding a session").
    /// Here that is every app that ever touched audio, playing or not, and an
    /// empty column says more than a column for Finder.
    pub fn top(&self, n: usize) -> Vec<String> {
        let mut out = self.active();
        for k in &self.seen {
            if !out.contains(k) && self.by_key.contains_key(k) {
                out.push(k.clone());
            }
        }
        out.truncate(n);
        out.resize(n, String::new());
        out
    }

    /// Stream peak, curved, then scaled by the app's own fader: the same shape
    /// as `audio.rs`, so a knob visibly moves its meter.
    pub fn levels(&self, keys: &[String], curve: f32) -> Vec<f32> {
        let s = self.shared.lock();
        keys.iter()
            .map(|k| {
                let Some(slot) = s.slots.get(k).filter(|_| !k.is_empty()) else { return 0.0 };
                let p = slot.peak();
                if p <= 0.0 {
                    return 0.0;
                }
                p.powf(curve) * s.prefs.get(k).copied().unwrap_or_default().effective()
            })
            .collect()
    }

    pub fn volume(&self, key: &str) -> f32 {
        self.shared.lock().prefs.get(key).copied().unwrap_or_default().gain
    }

    fn update(&self, key: &str, f: impl FnOnce(&mut Pref)) {
        let mut s = self.shared.lock();
        let pref = s.prefs.entry(key.to_string()).or_default();
        let before = *pref;
        f(pref);
        let after = *pref;
        if before == after {
            return;
        }
        s.prefs_dirty = true;
        slot_for(&mut s, key).target.store(after.effective().to_bits(), Ordering::Relaxed);
        s.wake = true;
        self.shared.cv.notify_one();
    }

    pub fn set_volume(&self, key: &str, v: f32) {
        self.update(key, |p| p.gain = v.clamp(0.0, 1.0));
    }

    pub fn muted(&self, key: &str) -> bool {
        self.shared.lock().prefs.get(key).copied().unwrap_or_default().muted
    }

    pub fn set_mute(&self, key: &str, muted: bool) {
        self.update(key, |p| p.muted = muted);
    }

    /// Diagnostics for `--sessions`: (key, rows of pid, running, peak, gain).
    pub fn session_table(&self) -> Vec<(String, Vec<(u32, bool, f32, f32)>)> {
        let s = self.shared.lock();
        let mut keys: Vec<&String> = self.by_key.keys().collect();
        keys.sort();
        keys.into_iter()
            .map(|k| {
                let peak = s.slots.get(k).map_or(0.0, |x| x.peak());
                let gain = s.prefs.get(k).copied().unwrap_or_default().effective();
                let rows = self.by_key[k].iter().map(|r| (r.pid as u32, r.running, peak, gain)).collect();
                (k.clone(), rows)
            })
            .collect()
    }

    // ---- system (default output) -------------------------------------------

    fn device(&self) -> Option<AudioObjectID> {
        self.output.as_ref().map(|o| o.id)
    }

    pub fn system_volume(&self) -> f32 {
        self.device().map_or(0.0, read_volume)
    }

    pub fn system_muted(&self) -> bool {
        self.device().is_some_and(read_mute)
    }

    pub fn set_system_volume(&self, f: f32) {
        if let Some(d) = self.device() {
            write_volume(d, f.clamp(0.0, 1.0));
        }
    }

    pub fn set_system_mute(&self, muted: bool) {
        if let Some(d) = self.device() {
            write_mute(d, muted);
        }
    }
}

/// Which elements carry a device property: the main one if it has it, else
/// its first two channels, which is where plenty of USB and built-in devices
/// put their volume.
fn elements(device: AudioObjectID, sel: u32) -> &'static [u32] {
    let out = kAudioObjectPropertyScopeOutput;
    if has(device, sel, out, kAudioObjectPropertyElementMain) {
        &[kAudioObjectPropertyElementMain]
    } else if has(device, sel, out, 1) {
        &[1, 2]
    } else {
        &[]
    }
}

fn read_volume(device: AudioObjectID) -> f32 {
    let sel = kAudioDevicePropertyVolumeScalar;
    let els = elements(device, sel);
    // A device with no volume at all (HDMI, most aggregates) is at full.
    let got: Vec<f32> = els
        .iter()
        .filter_map(|e| get::<f32>(device, sel, kAudioObjectPropertyScopeOutput, *e))
        .collect();
    if got.is_empty() {
        1.0
    } else {
        got.iter().cloned().fold(0.0, f32::max)
    }
}

fn write_volume(device: AudioObjectID, v: f32) {
    let sel = kAudioDevicePropertyVolumeScalar;
    let out = kAudioObjectPropertyScopeOutput;
    for e in elements(device, sel) {
        if settable(device, sel, out, *e) {
            set(device, sel, out, *e, v);
        }
    }
}

fn read_mute(device: AudioObjectID) -> bool {
    let sel = kAudioDevicePropertyMute;
    elements(device, sel)
        .first()
        .and_then(|e| get::<u32>(device, sel, kAudioObjectPropertyScopeOutput, *e))
        .is_some_and(|m| m != 0)
}

fn write_mute(device: AudioObjectID, muted: bool) {
    let sel = kAudioDevicePropertyMute;
    let out = kAudioObjectPropertyScopeOutput;
    for e in elements(device, sel) {
        if settable(device, sel, out, *e) {
            set(device, sel, out, *e, muted as u32);
        }
    }
}

/// The default output's `(UID, name)`. The UID is the stable identity: an
/// `AudioDeviceID` does not survive a reconnect.
pub fn default_endpoint() -> Option<(String, String)> {
    default_output().map(|o| (o.uid, o.name))
}

pub fn default_endpoint_name() -> Option<String> {
    default_output().map(|o| o.name)
}

pub fn to_pct(v: f32) -> i32 {
    (v.clamp(0.0, 1.0) * 100.0 + 0.5) as i32
}

// ---- the spectrum's capture -------------------------------------------------------

type Started = mpsc::Sender<Result<(u32, String), String>>;

/// A global tap on everything but this process, unmuted, on its own thread.
/// Sends `(rate, name)` or why not, then feeds `push` until `run` goes false or
/// the default output changes -- `SysMeter::restart_if_dead` reopens it then.
pub fn capture_loop(
    tx: Started,
    run: Arc<AtomicBool>,
    push: impl Fn(&[f32]),
) {
    let started = autoreleasepool(|_| {
        let out = default_output().ok_or("no default output device")?;
        let scope = kAudioObjectPropertyScopeGlobal;
        let main = kAudioObjectPropertyElementMain;
        // A re-rendered app would otherwise be counted twice: once at its
        // source, and again when this process plays it back.
        let pid = std::process::id() as i32;
        let a = addr(kAudioHardwarePropertyTranslatePIDToProcessObject, scope, main);
        let mut me: AudioObjectID = 0;
        let mut size = size_of::<AudioObjectID>() as u32;
        let st = unsafe {
            AudioObjectGetPropertyData(
                SYSTEM,
                NonNull::from(&a),
                size_of::<i32>() as u32,
                &pid as *const i32 as *const c_void,
                NonNull::from(&mut size),
                NonNull::from(&mut me).cast(),
            )
        };
        let exclude = if st == 0 && me != 0 { vec![me] } else { Vec::new() };
        let slot = Arc::new(Slot::new(1.0));
        Unit::build("spectrum", Spec::AllBut(exclude), false, &out, slot, true).map(|u| (u, out))
    });
    let (unit, out) = match started {
        Ok(v) => v,
        Err(e) => {
            let _ = tx.send(Err(format!("no system tap: {e}")));
            return;
        }
    };
    let _ = tx.send(Ok((out.rate as u32, out.name.clone())));

    let mut cursor = 0usize;
    let mut chunk = Vec::with_capacity(8192);
    // Whatever the last capture left in the ring is not this one's audio.
    SPECTRUM.drain(&mut cursor, &mut chunk);
    let mut checked = Instant::now();
    while run.load(Ordering::SeqCst) {
        SPECTRUM.drain(&mut cursor, &mut chunk);
        if !chunk.is_empty() {
            push(&chunk);
        }
        std::thread::sleep(Duration::from_millis(15));
        if checked.elapsed() >= SCAN {
            checked = Instant::now();
            if default_output().is_none_or(|o| o.uid != out.uid || o.rate != out.rate) {
                break;
            }
        }
    }
    drop(unit);
}

// ---- diagnostics --------------------------------------------------------------------

/// `--doctor`: everything worth knowing when audio does nothing on a Mac, since
/// nearly every failure here is silence rather than an error. Runs the real
/// engine, so an app with a saved knob position goes through the render path
/// exactly as it would under the daemon.
pub fn doctor() {
    println!("naubd doctor  (exe: {})", std::env::current_exe().map(|p| p.display().to_string()).unwrap_or_default());
    match default_output() {
        Some(o) => println!(
            "output:  {} | uid {} | {} Hz | {} output stream(s) | stereo pair {},{} | bluetooth {}",
            o.name, o.uid, o.rate, o.streams, o.left, o.right, o.bluetooth
        ),
        None => println!("output:  NONE"),
    }
    if let Some(o) = default_output() {
        let sel = kAudioDevicePropertyVolumeScalar;
        println!(
            "volume:  {:.2} elements {:?} settable {} | mute {}",
            read_volume(o.id),
            elements(o.id, sel),
            elements(o.id, sel)
                .first()
                .is_some_and(|e| settable(o.id, sel, kAudioObjectPropertyScopeOutput, *e)),
            read_mute(o.id)
        );
    }
    println!("\nserial:");
    for (dev, desc) in crate::link::list_ports() {
        println!("  {dev:<34} {desc}");
    }
    println!("  chosen as the data port: {:?}", crate::link::autodetect());

    let scope = kAudioObjectPropertyScopeGlobal;
    let main = kAudioObjectPropertyElementMain;
    println!("\naudio process objects (pid, output?, key <- path):");
    let t0 = Instant::now();
    let ids = get_ids(SYSTEM, kAudioHardwarePropertyProcessObjectList, scope);
    for id in &ids {
        let pid = get::<i32>(*id, kAudioProcessPropertyPID, scope, main).unwrap_or(0);
        let on = get::<u32>(*id, kAudioProcessPropertyIsRunningOutput, scope, main).unwrap_or(0) != 0;
        let path = exe_path(pid).unwrap_or_default();
        let key = appkey::from_path(&path).map_or("-".to_string(), |(k, _)| k);
        println!("  {pid:<7} {:<4} {key:<18} <- {path}", if on { "OUT" } else { "" });
    }
    println!("  ({} objects, scanned in {} ms)", ids.len(), t0.elapsed().as_millis());

    println!("\nwatching for 12 s; PLAY SOMETHING now. A first run should raise the permission prompt.");
    let mut a = match Audio::new() {
        Ok(a) => a,
        Err(e) => {
            println!("engine failed to start: {e}");
            return;
        }
    };
    for _ in 0..12 {
        std::thread::sleep(Duration::from_secs(1));
        a.refresh();
        let s = a.shared.lock();
        let mut units: Vec<String> = s
            .modes
            .iter()
            .map(|(k, m)| {
                let p = s.slots.get(k).map_or(0.0, |x| x.peak());
                format!("{k}[{m} peak={p:.3}]")
            })
            .collect();
        units.sort();
        println!("  capture_ok={} {}", capture_ok(), units.join(" "));
    }
    println!(
        "\n{}",
        if capture_ok() {
            "RESULT: taps deliver real audio. Meters, knobs and EQ should all work.".to_string()
        } else {
            "RESULT: no samples seen. If something WAS audibly playing, this process has no \
             audio-capture grant: System Settings > Privacy & Security > Screen & System Audio \
             Recording. Then quit and rerun; a grant only applies to a fresh process."
                .to_string()
        }
    );
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_pref_only_counts_as_touched_when_it_changes_the_sound() {
        assert!(!Pref::default().touched());
        assert!(Pref { gain: 0.5, muted: false }.touched());
        assert!(Pref { gain: 1.0, muted: true }.touched());
        assert_eq!(Pref { gain: 0.5, muted: true }.effective(), 0.0);
    }

    #[test]
    fn status_codes_read_as_people_quote_them() {
        assert_eq!(fourcc(0x21686F67), "'!hog'");
        assert_eq!(fourcc(-50), "-50");
    }
}
