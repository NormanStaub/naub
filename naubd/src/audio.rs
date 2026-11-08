//! Windows per-app + system audio via WASAPI (windows-rs COM).
//!
//! The OS adapter boundary -- all COM/unsafe lives here. The orchestrator talks
//! to it by string key (lowercased exe basename with `.exe` stripped, matching
//! the keys the device and `pins.json` use) and in 0..1 floats or 0..100
//! integers, so no COM pointers leak upward. COM is apartment-bound, so an
//! `Audio` must be created and used on a single thread.
//!
//! A port of `firmware/v2/host/appmeter.py`. Four things it learned the hard
//! way, each of which is load-bearing here:
//!
//! * **Level, yes -- but the peak is the RAW STREAM.** `IAudioMeterInformation`
//!   gives a real per-session amplitude, and measurably does *not* move when
//!   the session or endpoint volume changes. A meter built on it alone would
//!   sit still while you turn the knob, so [`Audio::levels`] scales by the
//!   session's own fader and reads zero when muted. The *system* volume is
//!   deliberately left out: that is not this app getting quieter.
//! * **One program is NOT one session.** Discord holds sessions from several
//!   pids and only one is audible -- measured, six times out of six, keying a
//!   map by name kept the silent one. Sessions are therefore GROUPED per
//!   program: the level is the loudest of them, and a volume or mute goes to
//!   ALL of them, because which one you can hear changes from call to call.
//! * **Nor is one endpoint all of them.** Apps can be pointed at any output
//!   device, so enumerating only the default one loses them. Sweeping every
//!   active render endpoint is also the *cheaper* option, provided the session
//!   managers are held open rather than rebuilt per poll.
//! * **The curve goes on the peak, and the fader multiplies the result.** The
//!   curve exists to lift quiet program material into view; running the fader
//!   through it too would compress the knob, making half volume read as 0.73
//!   of full height.

use std::collections::HashMap;
use std::time::{Duration, Instant};

use windows::core::{Interface, Result, PWSTR};
use windows::Win32::Foundation::CloseHandle;
use windows::Win32::Media::Audio::Endpoints::{IAudioEndpointVolume, IAudioMeterInformation};
use windows::Win32::Media::Audio::{
    eConsole, eRender, AudioSessionStateActive, IAudioSessionControl, IAudioSessionControl2,
    IAudioSessionManager2, IMMDevice, IMMDeviceEnumerator, ISimpleAudioVolume, MMDeviceEnumerator,
    DEVICE_STATE_ACTIVE,
};
use windows::Win32::System::Com::{
    CoCreateInstance, CoInitializeEx, CoTaskMemFree, CLSCTX_ALL, CLSCTX_INPROC_SERVER,
    COINIT_MULTITHREADED, STGM_READ,
};
use windows::Win32::System::Threading::{
    GetCurrentProcessId, OpenProcess, QueryFullProcessImageNameW, PROCESS_NAME_WIN32,
    PROCESS_QUERY_LIMITED_INFORMATION,
};

/// Channel 3 is the default output endpoint, for mute as well as volume.
pub const SYSTEM_CH: u8 = 3;

/// Peak is a linear amplitude, and speech and music sit low enough in that
/// range to look dead on a small panel. 1.0 disables the lift.
pub const CURVE: f32 = 0.45;

/// How often to look for newly plugged-in output devices.
const ENDPOINT_REBUILD: Duration = Duration::from_secs(15);

struct Session {
    ctl: IAudioSessionControl,
    meter: IAudioMeterInformation,
    vol: ISimpleAudioVolume,
    pid: u32,
}

impl Session {
    fn active(&self) -> bool {
        matches!(unsafe { self.ctl.GetState() }, Ok(s) if s == AudioSessionStateActive)
    }

    fn peak(&self) -> f32 {
        unsafe { self.meter.GetPeakValue().unwrap_or(0.0) }
    }

    /// This session's fader, 0 when muted. Cheap enough to read every frame,
    /// which is what makes the meter answer the knob immediately rather than a
    /// second later when the state poll comes round.
    fn gain(&self) -> f32 {
        unsafe {
            if self.vol.GetMute().map(|m| m.as_bool()).unwrap_or(false) {
                return 0.0;
            }
            self.vol.GetMasterVolume().unwrap_or(1.0)
        }
    }
}

pub struct Audio {
    enumerator: IMMDeviceEnumerator,
    /// One session manager per active render endpoint, HELD open: building them
    /// is the expensive part, re-enumerating from them is not.
    mgrs: Vec<IAudioSessionManager2>,
    built: Instant,
    endpoint: Option<IAudioEndpointVolume>,
    /// The default endpoint's registry GUID, as of the last refresh.
    default_guid: Option<String>,
    /// Program key -> every session that program holds, pid-sorted for a stable
    /// read. Insertion order is not meaningful; `seen` carries the ordering.
    by_key: HashMap<String, Vec<Session>>,
    /// Keys that have ever rendered audio, longest-established first. This is
    /// what makes the switcher's list stable instead of reshuffling per poll.
    seen: Vec<String>,
    /// pid -> process name, so the process is opened once and not per frame.
    pname: HashMap<u32, String>,
    own_pid: u32,
    fail: Option<String>,
}

impl Audio {
    pub fn new() -> Result<Self> {
        unsafe {
            // S_FALSE (already initialized on this thread) is fine.
            let _ = CoInitializeEx(None, COINIT_MULTITHREADED);
            let enumerator: IMMDeviceEnumerator =
                CoCreateInstance(&MMDeviceEnumerator, None, CLSCTX_ALL)?;
            let mut a = Self {
                enumerator,
                mgrs: Vec::new(),
                built: Instant::now() - ENDPOINT_REBUILD * 2,
                endpoint: None,
                default_guid: None,
                by_key: HashMap::new(),
                seen: Vec::new(),
                pname: HashMap::new(),
                own_pid: GetCurrentProcessId(),
                fail: None,
            };
            a.rebuild_endpoints();
            a.refresh();
            Ok(a)
        }
    }

    /// The default output's registry GUID, from the last refresh: free to ask
    /// once a second, unlike [`default_endpoint`], which builds an enumerator.
    pub fn default_guid(&self) -> Option<&str> {
        self.default_guid.as_deref()
    }

    /// Why the last refresh came back empty, if it did.
    pub fn failure(&self) -> Option<&str> {
        self.fail.as_deref()
    }

    fn rebuild_endpoints(&mut self) {
        let mut mgrs = Vec::new();
        if let Ok(coll) =
            unsafe { self.enumerator.EnumAudioEndpoints(eRender, DEVICE_STATE_ACTIVE) }
        {
            let n = unsafe { coll.GetCount().unwrap_or(0) };
            for i in 0..n {
                if let Ok(dev) = unsafe { coll.Item(i) } {
                    // Activate hands back a counted reference; unlike a raw
                    // pointer cast it cannot be left dangling when the device
                    // it came from is released.
                    if let Ok(m) = unsafe { dev.Activate::<IAudioSessionManager2>(CLSCTX_ALL, None) }
                    {
                        mgrs.push(m); // one dud device is not fatal
                    }
                }
            }
        }
        self.mgrs = mgrs;
        self.built = Instant::now();
    }

    /// Re-enumerate sessions and re-acquire the default endpoint. Cheap enough
    /// for ~1 Hz; peaks are polled from the cached objects and do not need it.
    ///
    /// Returns false rather than failing when WASAPI is mid-change: the default
    /// endpoint being torn down and recreated (headphones plugged in, a
    /// Bluetooth speaker connecting, the Equalizer APO configurator doing its
    /// work) makes every call on the old device fail for a moment. That is a
    /// normal event, so the previous session map is kept and retried next time.
    pub fn refresh(&mut self) -> bool {
        if self.built.elapsed() > ENDPOINT_REBUILD {
            self.rebuild_endpoints();
        }
        if let Ok(dev) = unsafe { self.enumerator.GetDefaultAudioEndpoint(eRender, eConsole) } {
            self.endpoint = unsafe { dev.Activate(CLSCTX_ALL, None).ok() };
            self.default_guid = endpoint_guid(&dev);
        }

        let mut by_key: HashMap<String, Vec<Session>> = HashMap::new();
        let mut any = false;
        // Cloned because the loop calls back into `self` to name processes.
        // These are COM interface handles: the clone is an AddRef, not a
        // re-enumeration, so it costs nothing worth avoiding.
        let mgrs = self.mgrs.clone();
        for m in &mgrs {
            let list = match unsafe { m.GetSessionEnumerator() } {
                Ok(l) => l,
                Err(_) => continue,
            };
            any = true;
            let count = unsafe { list.GetCount().unwrap_or(0) };
            for i in 0..count {
                let ctl = match unsafe { list.GetSession(i) } {
                    Ok(c) => c,
                    Err(_) => continue,
                };
                let ctl2: IAudioSessionControl2 = match ctl.cast() {
                    Ok(c) => c,
                    Err(_) => continue,
                };
                // PID 0 is the system-sounds / no-process session: notification
                // and error beeps, which must never become a mixer column.
                //
                // This is the pid test rather than IsSystemSoundsSession()
                // because that method answers S_OK / S_FALSE, and windows-rs
                // folds both into Ok(()) -- the distinction the call exists to
                // make does not survive the binding.
                let pid = unsafe { ctl2.GetProcessId().unwrap_or(0) };
                if pid == 0 {
                    continue;
                }
                // Skip OUR OWN process. The loopback capture opens the render
                // endpoint, which registers this daemon as an audio session --
                // so naub showed up in its own mixer, metering the system
                // output it was capturing. A tool should not appear among the
                // things it controls.
                if pid == self.own_pid {
                    continue;
                }
                let (meter, vol) = match (ctl.cast(), ctl.cast()) {
                    (Ok(m), Ok(v)) => (m, v),
                    _ => continue,
                };
                let key = self.name_of(pid);
                let sess = Session { ctl: ctl.clone(), meter, vol, pid };
                let active = sess.active();
                // APPEND, never overwrite. One program can hold several
                // sessions and only one may be audible; keeping whichever was
                // enumerated last is how Discord ended up listed with no level
                // and a dead volume knob.
                by_key.entry(key.clone()).or_default().push(sess);
                if active && !self.seen.iter().any(|k| *k == key) {
                    self.seen.push(key);
                }
            }
        }
        if !any && !self.mgrs.is_empty() {
            self.fail = Some("no session enumerator (device changing)".into());
            // Every manager failing at once is the audio service having been
            // restarted (attaching the EQ does that) and these being dead,
            // not a device mid-change: rebuild next pass, not in 15 s.
            self.built = Instant::now() - ENDPOINT_REBUILD * 2;
            return false;
        }
        self.fail = None;
        for g in by_key.values_mut() {
            g.sort_by_key(|s| s.pid); // stable pick for reads
        }
        self.by_key = by_key;
        true
    }

    fn name_of(&mut self, pid: u32) -> String {
        if let Some(n) = self.pname.get(&pid) {
            return n.clone();
        }
        let n = exe_path_for_pid(pid)
            .map(|p| app_key(&p))
            .unwrap_or_else(|| format!("pid{pid}"));
        if self.pname.len() > 256 {
            self.pname.clear(); // a long uptime is not a leak
        }
        self.pname.insert(pid, n.clone());
        n
    }

    /// The session to READ a program's volume from: one that is actually
    /// playing if there is one, since a silent sibling's fader tells you
    /// nothing about what you are hearing.
    fn rep(&self, key: &str) -> Option<&Session> {
        let g = self.by_key.get(key)?;
        g.iter().find(|s| s.active()).or_else(|| g.first())
    }

    /// The exe behind a program key, for its icon.
    pub fn exe_of(&self, key: &str) -> Option<String> {
        exe_path_for_pid(self.by_key.get(key)?.first()?.pid)
    }

    /// Every app with a live session right now, longest-established first.
    /// This is the switcher's list and its counter is the length of it, so it
    /// must be neither padded nor truncated.
    pub fn active(&self) -> Vec<String> {
        let live: Vec<&String> = self
            .by_key
            .iter()
            .filter(|(_, g)| g.iter().any(|s| s.active()))
            .map(|(k, _)| k)
            .collect();
        let mut out: Vec<String> = self
            .seen
            .iter()
            .filter(|k| live.contains(k))
            .cloned()
            .collect();
        for k in live {
            if !out.contains(k) {
                out.push(k.clone());
            }
        }
        out
    }

    /// Exactly `n` apps worth showing: currently rendering first, then ones
    /// that have played before (so a paused player keeps its column), then
    /// anything else holding a session. Padded with "" -- an empty column is a
    /// legitimate state, not an error.
    pub fn top(&self, n: usize) -> Vec<String> {
        let mut out = self.active();
        for k in &self.seen {
            if !out.contains(k) && self.by_key.contains_key(k) {
                out.push(k.clone());
            }
        }
        let mut rest: Vec<&String> = self.by_key.keys().filter(|k| !out.contains(k)).collect();
        rest.sort();
        for k in rest {
            out.push(k.clone());
        }
        out.truncate(n);
        while out.len() < n {
            out.push(String::new());
        }
        out
    }

    /// What each app actually SOUNDS like: its stream peak, curved, then scaled
    /// by its own fader. The LOUDEST of a program's sessions wins, each scaled
    /// by its own fader first since they can differ -- a program with a silent
    /// session open alongside the one you can hear should meter what you hear.
    pub fn levels(&self, keys: &[String], curve: f32) -> Vec<f32> {
        keys.iter()
            .map(|k| {
                let g = match self.by_key.get(k.as_str()) {
                    Some(g) if !k.is_empty() => g,
                    _ => return 0.0,
                };
                let mut best = 0.0f32;
                for s in g {
                    let p = s.peak();
                    if p <= 0.0 {
                        continue;
                    }
                    let v = p.powf(curve) * s.gain();
                    if v > best {
                        best = v;
                    }
                }
                best
            })
            .collect()
    }

    pub fn volume(&self, key: &str) -> f32 {
        match self.rep(key) {
            Some(s) => unsafe { s.vol.GetMasterVolume().unwrap_or(0.0) },
            None => 0.0,
        }
    }

    /// To EVERY session the program has open. Which one is audible can change
    /// from moment to moment -- Discord opens one per call -- and a knob that
    /// moved only whichever we happened to read from would work sometimes and
    /// do nothing the rest of the time.
    pub fn set_volume(&self, key: &str, f: f32) {
        let v = f.clamp(0.0, 1.0);
        for s in self.by_key.get(key).into_iter().flatten() {
            unsafe {
                let _ = s.vol.SetMasterVolume(v, std::ptr::null());
            }
        }
    }

    pub fn muted(&self, key: &str) -> bool {
        match self.rep(key) {
            Some(s) => unsafe { s.vol.GetMute().map(|m| m.as_bool()).unwrap_or(false) },
            None => false,
        }
    }

    pub fn set_mute(&self, key: &str, muted: bool) {
        for s in self.by_key.get(key).into_iter().flatten() {
            unsafe {
                let _ = s.vol.SetMute(muted, std::ptr::null());
            }
        }
    }

    /// Diagnostics for `--sessions`: (key, pid, active, peak, gain) per session.
    pub fn session_table(&self) -> Vec<(String, Vec<(u32, bool, f32, f32)>)> {
        let mut keys: Vec<&String> = self.by_key.keys().collect();
        keys.sort();
        keys.into_iter()
            .map(|k| {
                let g = &self.by_key[k];
                // Peak is "loudest since the last read", so a single sample can
                // land between buffers and read 0 on a session that is playing.
                let rows = g
                    .iter()
                    .map(|s| {
                        let p = (0..4).map(|_| s.peak()).fold(0.0f32, f32::max);
                        (s.pid, s.active(), p, s.gain())
                    })
                    .collect();
                (k.clone(), rows)
            })
            .collect()
    }

    // ---- system (default endpoint) ----

    pub fn system_volume(&self) -> f32 {
        self.endpoint
            .as_ref()
            .and_then(|ep| unsafe { ep.GetMasterVolumeLevelScalar().ok() })
            .unwrap_or(0.0)
    }

    pub fn system_muted(&self) -> bool {
        self.endpoint
            .as_ref()
            .and_then(|ep| unsafe { ep.GetMute().ok() })
            .map(|m| m.as_bool())
            .unwrap_or(false)
    }

    pub fn set_system_volume(&self, f: f32) {
        if let Some(ep) = &self.endpoint {
            unsafe {
                let _ = ep.SetMasterVolumeLevelScalar(f.clamp(0.0, 1.0), std::ptr::null());
            }
        }
    }

    pub fn set_system_mute(&self, muted: bool) {
        if let Some(ep) = &self.endpoint {
            unsafe {
                let _ = ep.SetMute(muted, std::ptr::null());
            }
        }
    }
}

/// The current default output endpoint, as `(guid, friendly name)`.
///
/// The GUID is the `{...}` that names the endpoint's key under
/// `MMDevices\Audio\Render`, and it is what `eqapo` must compare against. The
/// name is only for people: Windows mints a NEW endpoint, with a new GUID and
/// the same "Speakers (High Definition Audio Device)" name, whenever the
/// driver is reinstalled. Matching by name is how the EQ went dead while the
/// status said nothing was wrong with the speakers.
pub fn default_endpoint() -> Option<(String, String)> {
    unsafe {
        let _ = CoInitializeEx(None, COINIT_MULTITHREADED);
        let enumerator: IMMDeviceEnumerator =
            CoCreateInstance(&MMDeviceEnumerator, None, CLSCTX_INPROC_SERVER).ok()?;
        let dev = enumerator.GetDefaultAudioEndpoint(eRender, eConsole).ok()?;
        let guid = endpoint_guid(&dev)?;
        let name = dev
            .OpenPropertyStore(STGM_READ)
            .and_then(|s| {
                s.GetValue(&windows::Win32::Devices::FunctionDiscovery::PKEY_Device_FriendlyName)
            })
            .map(|v| v.to_string())
            .unwrap_or_default();
        let name = if name.is_empty() { guid.clone() } else { name };
        Some((guid, name))
    }
}

/// "{0.0.0.00000000}.{guid}" -> "{guid}", lowercased: the endpoint's key name
/// under `MMDevices\Audio\Render`.
fn endpoint_guid(dev: &IMMDevice) -> Option<String> {
    unsafe {
        let raw = dev.GetId().ok()?;
        let id = raw.to_string().unwrap_or_default();
        CoTaskMemFree(Some(raw.0 as *const _));
        id.rsplit('.').next().filter(|g| g.starts_with('{')).map(|g| g.to_ascii_lowercase())
    }
}

/// Just the name, for anything that only has to tell a person which device.
pub fn default_endpoint_name() -> Option<String> {
    default_endpoint().map(|(_, name)| name)
}

/// The key a column, a pin and the device all agree on: the exe basename,
/// lowercased, with `.exe` stripped ("spotify", not "Spotify.exe").
fn app_key(path: &str) -> String {
    let base = path.rsplit(['\\', '/']).next().unwrap_or(path).to_ascii_lowercase();
    base.strip_suffix(".exe").unwrap_or(&base).to_string()
}

fn exe_path_for_pid(pid: u32) -> Option<String> {
    unsafe {
        let handle = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, false, pid).ok()?;
        let mut buf = [0u16; 260];
        let mut len = buf.len() as u32;
        let ok = QueryFullProcessImageNameW(handle, PROCESS_NAME_WIN32, PWSTR(buf.as_mut_ptr()), &mut len);
        let _ = CloseHandle(handle);
        ok.ok()?;
        Some(String::from_utf16_lossy(&buf[..len as usize]))
    }
}

/// 0..1 float -> the 0..100 the wire and the panel use.
pub fn to_pct(v: f32) -> i32 {
    (v.clamp(0.0, 1.0) * 100.0 + 0.5) as i32
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn keys_match_the_device_and_the_pin_file() {
        assert_eq!(app_key(r"C:\Program Files\Spotify\Spotify.exe"), "spotify");
        assert_eq!(app_key("/usr/bin/Chrome.EXE"), "chrome");
        assert_eq!(app_key("gtaiv.exe"), "gtaiv");
        // No extension is not an error; the basename stands on its own.
        assert_eq!(app_key(r"D:\games\Red Alert"), "red alert");
    }

    #[test]
    fn pct_rounds_half_up_and_clamps() {
        assert_eq!(to_pct(0.0), 0);
        assert_eq!(to_pct(1.0), 100);
        assert_eq!(to_pct(0.005), 1);
        assert_eq!(to_pct(2.0), 100);
        assert_eq!(to_pct(-1.0), 0);
    }
}
