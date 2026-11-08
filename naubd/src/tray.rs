//! System-tray shell around the daemon: a hidden message window owns a tray icon
//! with a right-click menu (Attach EQ when the current output lacks it,
//! Start-with-Windows toggle, Quit). The audio/serial
//! work runs on a worker thread (COM is initialized there); this thread just
//! pumps Win32 messages.

use windows::core::{w, HSTRING, PCWSTR};
use windows::Win32::Foundation::{HINSTANCE, HWND, LPARAM, LRESULT, POINT, WPARAM};
use windows::Win32::System::LibraryLoader::GetModuleHandleW;
use windows::Win32::UI::Shell::{
    Shell_NotifyIconW, NIF_ICON, NIF_MESSAGE, NIF_TIP, NIM_ADD, NIM_DELETE, NOTIFYICONDATAW,
};
use windows::Win32::UI::WindowsAndMessaging::{
    AppendMenuW, CreatePopupMenu, CreateWindowExW, DefWindowProcW, DestroyMenu, DispatchMessageW,
    GetCursorPos, GetMessageW, LoadIconW, PostQuitMessage, RegisterClassExW, SetForegroundWindow,
    TrackPopupMenu, TranslateMessage, IDI_APPLICATION, MF_CHECKED, MF_GRAYED, MF_SEPARATOR,
    MF_STRING, MF_UNCHECKED, MSG, TPM_RETURNCMD, TPM_RIGHTBUTTON, WINDOW_EX_STYLE, WINDOW_STYLE,
    WM_APP, WM_DESTROY, WM_LBUTTONUP, WM_RBUTTONUP, WNDCLASSEXW,
};

use winreg::enums::HKEY_CURRENT_USER;
use winreg::RegKey;

use crate::bridge::Bridge;
use crate::config::Config;
use crate::{admin, eqapo, logging};

const TRAY_CALLBACK: u32 = WM_APP + 1;
const TRAY_UID: u32 = 1;
const ID_STATUS: usize = 1;
const ID_AUTOSTART: usize = 2;
const ID_QUIT: usize = 3;
const ID_EQ_ATTACH: usize = 4;

const RUN_KEY: &str = r"Software\Microsoft\Windows\CurrentVersion\Run";
const APP_NAME: &str = "naubd";

/// Spawn the daemon on a worker thread, then run the tray message loop.
///
/// The bridge is built ON that thread, not here: COM is apartment-bound, and
/// the WASAPI objects have to live where they are used.
pub fn run(cfg: Config, verbose: bool) {
    std::thread::spawn(move || match Bridge::new(cfg.port, cfg.rate, verbose) {
        Ok(mut b) => b.run_forever(),
        Err(e) => logging::line(&format!("audio init failed: {e}")),
    });

    unsafe {
        if let Err(e) = message_loop() {
            logging::line(&format!("tray failed: {e}"));
        }
    }
}

unsafe fn message_loop() -> windows::core::Result<()> {
    let hinstance: HINSTANCE = GetModuleHandleW(None)?.into();
    let class = w!("naubd_tray");
    let wc = WNDCLASSEXW {
        cbSize: std::mem::size_of::<WNDCLASSEXW>() as u32,
        lpfnWndProc: Some(wndproc),
        hInstance: hinstance,
        lpszClassName: class,
        ..Default::default()
    };
    RegisterClassExW(&wc);

    // Message-only window (no taskbar/visible window) to receive tray callbacks.
    let hwnd = CreateWindowExW(
        WINDOW_EX_STYLE(0),
        class,
        w!("naubd"),
        WINDOW_STYLE(0),
        0,
        0,
        0,
        0,
        None,
        None,
        hinstance,
        None,
    )?;

    add_tray_icon(hwnd)?;

    let mut msg = MSG::default();
    while GetMessageW(&mut msg, None, 0, 0).as_bool() {
        let _ = TranslateMessage(&msg);
        DispatchMessageW(&msg);
    }
    remove_tray_icon(hwnd);
    Ok(())
}

unsafe fn add_tray_icon(hwnd: HWND) -> windows::core::Result<()> {
    let mut nid = NOTIFYICONDATAW {
        cbSize: std::mem::size_of::<NOTIFYICONDATAW>() as u32,
        hWnd: hwnd,
        uID: TRAY_UID,
        uFlags: NIF_ICON | NIF_MESSAGE | NIF_TIP,
        uCallbackMessage: TRAY_CALLBACK,
        hIcon: LoadIconW(None, IDI_APPLICATION)?,
        ..Default::default()
    };
    let tip = "naub mixer";
    for (i, c) in tip.encode_utf16().enumerate() {
        nid.szTip[i] = c;
    }
    let _ = Shell_NotifyIconW(NIM_ADD, &nid);
    Ok(())
}

unsafe fn remove_tray_icon(hwnd: HWND) {
    let nid = NOTIFYICONDATAW {
        cbSize: std::mem::size_of::<NOTIFYICONDATAW>() as u32,
        hWnd: hwnd,
        uID: TRAY_UID,
        ..Default::default()
    };
    let _ = Shell_NotifyIconW(NIM_DELETE, &nid);
}

unsafe extern "system" fn wndproc(hwnd: HWND, msg: u32, wp: WPARAM, lp: LPARAM) -> LRESULT {
    match msg {
        TRAY_CALLBACK => {
            let event = lp.0 as u32;
            if event == WM_RBUTTONUP || event == WM_LBUTTONUP {
                show_menu(hwnd);
            }
            LRESULT(0)
        }
        WM_DESTROY => {
            PostQuitMessage(0);
            LRESULT(0)
        }
        _ => DefWindowProcW(hwnd, msg, wp, lp),
    }
}

unsafe fn show_menu(hwnd: HWND) {
    let menu = match CreatePopupMenu() {
        Ok(m) => m,
        Err(_) => return,
    };
    let _ = AppendMenuW(menu, MF_STRING | MF_GRAYED, ID_STATUS, w!("naub mixer"));
    let _ = AppendMenuW(menu, MF_SEPARATOR, 0, PCWSTR::null());
    // Asked fresh each time the menu opens: the default output may have
    // changed since. On a throwaway thread so COM stays off this one.
    let eq = std::thread::spawn(|| {
        if !eqapo::installed() {
            return None;
        }
        crate::audio::default_endpoint().map(|(guid, name)| (eqapo::hosts_apo(&guid), guid, name))
    })
    .join()
    .ok()
    .flatten();
    let eq_label;
    if let Some((hosted, _, name)) = &eq {
        let (flags, text) = if *hosted {
            (MF_STRING | MF_GRAYED | MF_CHECKED, format!("EQ active on {name}"))
        } else {
            (MF_STRING, format!("Attach EQ to {name}"))
        };
        eq_label = HSTRING::from(text);
        let _ = AppendMenuW(menu, flags, ID_EQ_ATTACH, PCWSTR(eq_label.as_ptr()));
    }
    let check = if autostart_enabled() { MF_CHECKED } else { MF_UNCHECKED };
    let _ = AppendMenuW(menu, MF_STRING | check, ID_AUTOSTART, w!("Start with Windows"));
    let _ = AppendMenuW(menu, MF_STRING, ID_QUIT, w!("Quit"));

    let mut pt = POINT::default();
    let _ = GetCursorPos(&mut pt);
    // Required so the menu dismisses correctly on click-away.
    let _ = SetForegroundWindow(hwnd);
    let cmd = TrackPopupMenu(menu, TPM_RIGHTBUTTON | TPM_RETURNCMD, pt.x, pt.y, 0, hwnd, None);
    let _ = DestroyMenu(menu);

    match cmd.0 as usize {
        ID_AUTOSTART => set_autostart(!autostart_enabled()),
        ID_EQ_ATTACH => {
            if let Some((false, guid, name)) = eq {
                attach_eq(guid, name);
            }
        }
        ID_QUIT => {
            remove_tray_icon(hwnd);
            PostQuitMessage(0);
        }
        _ => {}
    }
}

/// Hand the attach to an elevated copy of this exe (UAC prompt), off the
/// message thread so the tray stays responsive while the prompt is up. Nothing
/// reports back here: the daemon's once-a-second EQ check sees the result and
/// tells the panel.
fn attach_eq(guid: String, name: String) {
    std::thread::spawn(move || {
        logging::line(&format!("eq-attach: {name} {guid}"));
        match admin::run_elevated(&["--eq-attach", &guid]) {
            Ok(0) => {}
            Ok(code) => logging::line(&format!("eq-attach: exit {code} (see the lines above)")),
            Err(e) => logging::line(&format!("eq-attach: {e}")),
        }
    });
}

// ---- autostart (HKCU Run key) ----

fn exe_command() -> String {
    let exe = std::env::current_exe().unwrap_or_default();
    format!("\"{}\"", exe.display())
}

pub fn autostart_enabled() -> bool {
    RegKey::predef(HKEY_CURRENT_USER)
        .open_subkey(RUN_KEY)
        .ok()
        .and_then(|k| k.get_value::<String, _>(APP_NAME).ok())
        .is_some()
}

pub fn set_autostart(enable: bool) {
    let run = match RegKey::predef(HKEY_CURRENT_USER).create_subkey(RUN_KEY) {
        Ok((k, _)) => k,
        Err(e) => {
            logging::line(&format!("autostart: open Run key failed: {e}"));
            return;
        }
    };
    let res = if enable {
        run.set_value(APP_NAME, &exe_command())
    } else {
        run.delete_value(APP_NAME)
    };
    match res {
        Ok(_) => logging::line(if enable {
            "autostart: enabled"
        } else {
            "autostart: disabled"
        }),
        Err(e) => logging::line(&format!("autostart: write failed: {e}")),
    }
}
