//! Windows per-app + system audio via WASAPI (windows-rs COM).
//!
//! The OS adapter boundary -- all COM/unsafe lives here. The orchestrator talks
//! to it by string key (lowercased exe basename, or `__system__`) and in 0..100
//! integer levels, so no COM pointers or lifetimes leak upward. COM is
//! apartment-bound, so an `Audio` must be created and used on a single thread.

use std::collections::HashSet;

use windows::core::{Interface, Result, PWSTR};
use windows::Win32::Foundation::CloseHandle;
use windows::Win32::Media::Audio::Endpoints::IAudioEndpointVolume;
use windows::Win32::Media::Audio::{
    eConsole, eRender, AudioSessionStateActive, IAudioSessionControl2,
    IAudioSessionEnumerator, IAudioSessionManager2, IMMDevice, IMMDeviceEnumerator,
    ISimpleAudioVolume, MMDeviceEnumerator,
};
use windows::Win32::System::Com::{
    CoCreateInstance, CoInitializeEx, CLSCTX_ALL, COINIT_MULTITHREADED,
};
use windows::Win32::System::Threading::{
    OpenProcess, QueryFullProcessImageNameW, PROCESS_NAME_WIN32,
    PROCESS_QUERY_LIMITED_INFORMATION,
};

pub const SYSTEM_KEY: &str = "__system__";

struct AppSession {
    key: String,      // lowercased exe basename, e.g. "spotify.exe"
    exe_path: String, // full path -> icon survives the app closing
    active: bool,     // session state == Active (rendering audio right now)
    vol: ISimpleAudioVolume,
}

pub struct Audio {
    enumerator: IMMDeviceEnumerator,
    endpoint: Option<IAudioEndpointVolume>, // default render endpoint (system vol)
    sessions: Vec<AppSession>,
    seen_active: HashSet<String>, // keys that have rendered audio this run
}

impl Audio {
    pub fn new() -> Result<Self> {
        unsafe {
            // S_FALSE (already initialized on this thread) is fine.
            let _ = CoInitializeEx(None, COINIT_MULTITHREADED);
            let enumerator: IMMDeviceEnumerator =
                CoCreateInstance(&MMDeviceEnumerator, None, CLSCTX_ALL)?;
            Ok(Self {
                enumerator,
                endpoint: None,
                sessions: Vec::new(),
                seen_active: HashSet::new(),
            })
        }
    }

    /// Re-enumerate app sessions and re-acquire the default endpoint (so we track
    /// the current default device + system volume).
    pub fn snapshot(&mut self) {
        self.sessions.clear();
        let device = match unsafe {
            self.enumerator.GetDefaultAudioEndpoint(eRender, eConsole)
        } {
            Ok(d) => d,
            Err(_) => {
                self.endpoint = None;
                return;
            }
        };
        self.endpoint = unsafe { device.Activate(CLSCTX_ALL, None).ok() };
        if let Err(_e) = self.enumerate_sessions(&device) {
            // Leave whatever we managed to gather; a transient COM error just
            // means a sparse snapshot this cycle.
        }
    }

    fn enumerate_sessions(&mut self, device: &IMMDevice) -> Result<()> {
        let mgr: IAudioSessionManager2 = unsafe { device.Activate(CLSCTX_ALL, None)? };
        let list: IAudioSessionEnumerator = unsafe { mgr.GetSessionEnumerator()? };
        let count = unsafe { list.GetCount()? };
        for i in 0..count {
            let ctl = match unsafe { list.GetSession(i) } {
                Ok(c) => c,
                Err(_) => continue,
            };
            let ctl2: IAudioSessionControl2 = match ctl.cast() {
                Ok(c) => c,
                Err(_) => continue,
            };
            // PID 0 == the system-sounds / no-process session -> skip (matches the
            // Python behavior; this also drops error/notification beeps).
            let pid = unsafe { ctl2.GetProcessId().unwrap_or(0) };
            if pid == 0 {
                continue;
            }
            let exe_path = match exe_path_for_pid(pid) {
                Some(p) => p,
                None => continue,
            };
            let key = basename_lower(&exe_path);
            let vol: ISimpleAudioVolume = match ctl.cast() {
                Ok(v) => v,
                Err(_) => continue,
            };
            let active = matches!(unsafe { ctl.GetState() }, Ok(s) if s == AudioSessionStateActive);
            if active {
                self.seen_active.insert(key.clone());
            }
            self.sessions.push(AppSession { key, exe_path, active, vol });
        }
        Ok(())
    }

    pub fn mark_seen(&mut self, key: &str) {
        if !key.is_empty() {
            self.seen_active.insert(key.to_ascii_lowercase());
        }
    }

    /// Hold-to-cycle pool: apps currently rendering OR that have rendered audio
    /// this run (so paused/idle media stays pickable). Stable first-seen order.
    pub fn active_app_keys(&self) -> Vec<String> {
        let mut keys = Vec::new();
        for s in &self.sessions {
            if (s.active || self.seen_active.contains(&s.key)) && !keys.contains(&s.key) {
                keys.push(s.key.clone());
            }
        }
        keys
    }

    fn find(&self, key: &str) -> Option<&AppSession> {
        self.sessions.iter().find(|s| s.key == key)
    }

    pub fn has_app(&self, key: &str) -> bool {
        self.find(key).is_some()
    }

    pub fn exe_path(&self, key: &str) -> Option<String> {
        self.find(key).map(|s| s.exe_path.clone())
    }

    /// (level 0..100, mute) for an app, or None if its session is gone.
    pub fn get_app(&self, key: &str) -> Option<(i32, bool)> {
        let s = self.find(key)?;
        let v = unsafe { s.vol.GetMasterVolume().ok()? };
        let m = unsafe { s.vol.GetMute().ok()? };
        Some((scalar_to_level(v), m.as_bool()))
    }

    pub fn set_app_volume(&self, key: &str, level: i32) -> bool {
        match self.find(key) {
            Some(s) => unsafe {
                s.vol.SetMasterVolume(level_to_scalar(level), std::ptr::null()).is_ok()
            },
            None => false,
        }
    }

    pub fn set_app_mute(&self, key: &str, muted: bool) -> bool {
        match self.find(key) {
            Some(s) => unsafe { s.vol.SetMute(muted, std::ptr::null()).is_ok() },
            None => false,
        }
    }

    // ---- system (default endpoint) ----

    pub fn get_system(&self) -> Option<(i32, bool)> {
        let ep = self.endpoint.as_ref()?;
        let v = unsafe { ep.GetMasterVolumeLevelScalar().ok()? };
        let m = unsafe { ep.GetMute().ok()? };
        Some((scalar_to_level(v), m.as_bool()))
    }

    pub fn set_system_volume(&self, level: i32) -> bool {
        match &self.endpoint {
            Some(ep) => unsafe {
                ep.SetMasterVolumeLevelScalar(level_to_scalar(level), std::ptr::null()).is_ok()
            },
            None => false,
        }
    }

    pub fn set_system_mute(&self, muted: bool) -> bool {
        match &self.endpoint {
            Some(ep) => unsafe { ep.SetMute(muted, std::ptr::null()).is_ok() },
            None => false,
        }
    }
}

fn scalar_to_level(v: f32) -> i32 {
    (v * 100.0).round().clamp(0.0, 100.0) as i32
}

fn level_to_scalar(level: i32) -> f32 {
    level.clamp(0, 100) as f32 / 100.0
}

fn basename_lower(path: &str) -> String {
    path.rsplit(['\\', '/']).next().unwrap_or(path).to_ascii_lowercase()
}

fn exe_path_for_pid(pid: u32) -> Option<String> {
    unsafe {
        let handle = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, false, pid).ok()?;
        let mut buf = [0u16; 260];
        let mut len = buf.len() as u32;
        let ok = QueryFullProcessImageNameW(
            handle,
            PROCESS_NAME_WIN32,
            PWSTR(buf.as_mut_ptr()),
            &mut len,
        );
        let _ = CloseHandle(handle);
        ok.ok()?;
        Some(String::from_utf16_lossy(&buf[..len as usize]))
    }
}
