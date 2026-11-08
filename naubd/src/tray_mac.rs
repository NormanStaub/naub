//! Menu-bar shell around the daemon: the macOS `tray.rs`. An accessory app (no
//! Dock icon) whose status item carries the same short menu. AppKit owns the
//! main thread; the audio/serial work runs on a worker, as on Windows.

use std::cell::OnceCell;

use objc2::rc::Retained;
use objc2::runtime::{AnyObject, NSObject, NSObjectProtocol};
use objc2::{define_class, msg_send, sel, DefinedClass, MainThreadMarker, MainThreadOnly};
use objc2_app_kit::{
    NSApplication, NSApplicationActivationPolicy, NSMenu, NSMenuDelegate, NSMenuItem, NSStatusBar,
    NSVariableStatusItemLength,
};
use objc2_foundation::NSString;
use objc2_service_management::{SMAppService, SMAppServiceStatus};

use crate::bridge::Bridge;
use crate::config::Config;
use crate::logging;

/// The pane Apple calls Screen & System Audio Recording.
const PRIVACY_URL: &str =
    "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture";

#[derive(Default)]
struct Items {
    status: OnceCell<Retained<NSMenuItem>>,
    login: OnceCell<Retained<NSMenuItem>>,
}

define_class!(
    #[unsafe(super(NSObject))]
    #[thread_kind = MainThreadOnly]
    #[name = "NaubMenuTarget"]
    #[ivars = Items]
    struct Target;

    unsafe impl NSObjectProtocol for Target {}

    unsafe impl NSMenuDelegate for Target {
        /// Asked fresh each time: the grant and the output can both have moved.
        #[unsafe(method(menuWillOpen:))]
        fn menu_will_open(&self, _menu: &NSMenu) {
            self.refresh();
        }
    }

    impl Target {
        #[unsafe(method(toggleLogin:))]
        fn toggle_login(&self, _sender: Option<&AnyObject>) {
            set_autostart(!autostart_enabled());
            self.refresh();
        }

        #[unsafe(method(openPrivacy:))]
        fn open_privacy(&self, _sender: Option<&AnyObject>) {
            let _ = std::process::Command::new("open").arg(PRIVACY_URL).status();
        }

        #[unsafe(method(quit:))]
        fn quit(&self, _sender: Option<&AnyObject>) {
            // Private taps and aggregates die with the process, and an app we
            // had muted at the source goes back to playing normally.
            std::process::exit(0);
        }
    }
);

impl Target {
    fn new(mtm: MainThreadMarker) -> Retained<Self> {
        let this = Self::alloc(mtm).set_ivars(Items::default());
        unsafe { msg_send![super(this), init] }
    }

    fn refresh(&self) {
        let ok = crate::audio::capture_ok();
        let name = crate::audio::default_endpoint_name().unwrap_or_else(|| "no output".into());
        let text = if ok {
            format!("naub: {name}")
        } else {
            "naub: audio capture not confirmed yet".to_string()
        };
        if let Some(i) = self.ivars().status.get() {
            i.setTitle(&NSString::from_str(&text));
        }
        if let Some(i) = self.ivars().login.get() {
            i.setState(autostart_enabled() as isize);
            i.setEnabled(in_app_bundle());
        }
    }
}

/// Spawn the daemon on a worker thread, then run AppKit here. Never returns.
pub fn run(cfg: Config, verbose: bool) {
    std::thread::spawn(move || match Bridge::new(cfg.port, cfg.rate, verbose) {
        Ok(mut b) => b.run_forever(),
        Err(e) => logging::line(&format!("audio init failed: {e}")),
    });

    let Some(mtm) = MainThreadMarker::new() else {
        logging::line("tray must start on the main thread");
        return;
    };
    let app = NSApplication::sharedApplication(mtm);
    app.setActivationPolicy(NSApplicationActivationPolicy::Accessory);

    let target = Target::new(mtm);
    let menu = NSMenu::new(mtm);
    menu.setAutoenablesItems(false);
    menu.setDelegate(Some(objc2::runtime::ProtocolObject::from_ref(&*target)));

    let item = |title: &str, action: Option<objc2::runtime::Sel>| -> Retained<NSMenuItem> {
        let it = unsafe {
            NSMenuItem::initWithTitle_action_keyEquivalent(
                NSMenuItem::alloc(mtm),
                &NSString::from_str(title),
                action,
                &NSString::from_str(""),
            )
        };
        if action.is_some() {
            unsafe { it.setTarget(Some(&target)) };
        }
        menu.addItem(&it);
        it
    };
    let status = item("naub", None);
    status.setEnabled(false);
    let _ = target.ivars().status.set(status);
    item("Audio Recording Permission...", Some(sel!(openPrivacy:)));
    menu.addItem(&NSMenuItem::separatorItem(mtm));
    let _ = target.ivars().login.set(item("Start at Login", Some(sel!(toggleLogin:))));
    item("Quit", Some(sel!(quit:)));

    let bar = NSStatusBar::systemStatusBar();
    let status_item = bar.statusItemWithLength(NSVariableStatusItemLength);
    if let Some(button) = status_item.button(mtm) {
        button.setTitle(&NSString::from_str("naub"));
    }
    status_item.setMenu(Some(&menu));
    target.refresh();

    app.run();
    // Kept alive by this frame for the app's whole life.
    drop((status_item, menu, target));
}

// ---- autostart (SMAppService) ----

/// `SMAppService` registers the enclosing app. A bare binary has none, and a
/// login item is also what keeps the audio grant attributed to naub rather
/// than to no one (MACOS_PORT.md §3.12), so this is bundle-only by design.
fn in_app_bundle() -> bool {
    std::env::current_exe()
        .map(|p| p.to_string_lossy().contains(".app/Contents/MacOS/"))
        .unwrap_or(false)
}

pub fn autostart_enabled() -> bool {
    if !in_app_bundle() {
        return false;
    }
    unsafe { SMAppService::mainAppService().status() == SMAppServiceStatus::Enabled }
}

pub fn set_autostart(enable: bool) {
    if !in_app_bundle() {
        logging::line("autostart: only available from naub.app (macos/build-app.sh)");
        return;
    }
    let svc = unsafe { SMAppService::mainAppService() };
    let res = unsafe {
        if enable {
            svc.registerAndReturnError()
        } else {
            svc.unregisterAndReturnError()
        }
    };
    match res {
        Ok(()) => logging::line(if enable { "autostart: enabled" } else { "autostart: disabled" }),
        Err(e) => logging::line(&format!("autostart: {e:?}")),
    }
}
