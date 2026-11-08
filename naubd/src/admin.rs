//! The two things naubd does as an administrator, both on request only:
//! re-launching itself elevated, and restarting the Windows audio service.
//!
//! The daemon itself never runs elevated. Attaching Equalizer APO to an output
//! device writes HKLM keys that only Administrators may set, so that one step
//! is handed to a short-lived elevated copy of this exe (`--eq-attach`), which
//! does it and exits.

use std::time::{Duration, Instant};

use windows::core::{HSTRING, PCWSTR};
use windows::Win32::Foundation::{CloseHandle, WAIT_OBJECT_0};
use windows::Win32::System::Services::{
    CloseServiceHandle, ControlService, OpenSCManagerW, OpenServiceW, QueryServiceStatus,
    StartServiceW, SC_MANAGER_CONNECT, SERVICE_CONTROL_STOP, SERVICE_QUERY_STATUS,
    SERVICE_RUNNING, SERVICE_START, SERVICE_STATUS, SERVICE_STOP, SERVICE_STOPPED,
};
use windows::Win32::System::Threading::{GetExitCodeProcess, WaitForSingleObject, INFINITE};
use windows::Win32::UI::Shell::{
    IsUserAnAdmin, ShellExecuteExW, SEE_MASK_NOCLOSEPROCESS, SHELLEXECUTEINFOW,
};
use windows::Win32::UI::WindowsAndMessaging::SW_HIDE;

pub fn is_admin() -> bool {
    unsafe { IsUserAnAdmin().as_bool() }
}

/// Run this exe again, elevated, with `args`; wait for it and return its exit
/// code. Shows the UAC prompt, so it blocks for as long as the user takes.
pub fn run_elevated(args: &[&str]) -> Result<u32, String> {
    let exe = std::env::current_exe().map_err(|e| e.to_string())?;
    let file = HSTRING::from(exe.as_os_str());
    let params = HSTRING::from(args.join(" "));
    let mut info = SHELLEXECUTEINFOW {
        cbSize: std::mem::size_of::<SHELLEXECUTEINFOW>() as u32,
        fMask: SEE_MASK_NOCLOSEPROCESS,
        lpVerb: windows::core::w!("runas"),
        lpFile: PCWSTR(file.as_ptr()),
        lpParameters: PCWSTR(params.as_ptr()),
        nShow: SW_HIDE.0,
        ..Default::default()
    };
    unsafe {
        // Declining the UAC prompt lands here as ERROR_CANCELLED.
        ShellExecuteExW(&mut info).map_err(|e| format!("not elevated: {}", e.message()))?;
        if info.hProcess.is_invalid() {
            return Err("elevated process did not start".into());
        }
        let mut code = 1u32;
        if WaitForSingleObject(info.hProcess, INFINITE) == WAIT_OBJECT_0 {
            let _ = GetExitCodeProcess(info.hProcess, &mut code);
        }
        let _ = CloseHandle(info.hProcess);
        Ok(code)
    }
}

/// Stop and start a service, waiting for each. Audiosrv takes well under a
/// second either way on the machine this was written on; the timeout is only
/// so a wedged service cannot hang the caller.
pub fn restart_service(name: &str) -> Result<(), String> {
    const TIMEOUT: Duration = Duration::from_secs(15);
    unsafe {
        let scm = OpenSCManagerW(PCWSTR::null(), PCWSTR::null(), SC_MANAGER_CONNECT)
            .map_err(|e| format!("service manager: {}", e.message()))?;
        let svc = OpenServiceW(
            scm,
            &HSTRING::from(name),
            SERVICE_STOP | SERVICE_START | SERVICE_QUERY_STATUS,
        );
        let svc = match svc {
            Ok(s) => s,
            Err(e) => {
                let _ = CloseServiceHandle(scm);
                return Err(format!("{name}: {}", e.message()));
            }
        };
        let wait_for = |want| -> bool {
            let t0 = Instant::now();
            let mut st = SERVICE_STATUS::default();
            while t0.elapsed() < TIMEOUT {
                if QueryServiceStatus(svc, &mut st).is_ok() && st.dwCurrentState == want {
                    return true;
                }
                std::thread::sleep(Duration::from_millis(50));
            }
            false
        };
        let mut st = SERVICE_STATUS::default();
        let result = (|| {
            // Already stopped is fine: the point is that it comes back up and
            // re-reads the endpoints' effect lists.
            let _ = ControlService(svc, SERVICE_CONTROL_STOP, &mut st);
            if !wait_for(SERVICE_STOPPED) {
                return Err(format!("{name} did not stop"));
            }
            StartServiceW(svc, None).map_err(|e| format!("{name} did not start: {}", e.message()))?;
            if !wait_for(SERVICE_RUNNING) {
                return Err(format!("{name} did not come back up"));
            }
            Ok(())
        })();
        let _ = CloseServiceHandle(svc);
        let _ = CloseServiceHandle(scm);
        result
    }
}
