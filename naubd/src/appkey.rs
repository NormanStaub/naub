//! From a macOS executable path to the key a column, a pin and the panel use.
//!
//! On Windows the key is the exe basename. The Mac equivalent is the OUTERMOST
//! `.app` in the path, because that is what a person thinks of as the app:
//! Chrome renders from `Google Chrome.app/.../Google Chrome Helper (Renderer).app`
//! and the helper is the process holding the audio. Taps are grouped per key,
//! as WASAPI sessions are per exe, and for the same reason.
//!
//! Compiled everywhere under `cfg(test)`, so this table can be tested without a
//! Mac: pure strings, no system calls.

#![cfg_attr(not(target_os = "macos"), allow(dead_code))]

/// Mac app name -> the key the Windows build already uses for that program, so
/// baked icons match and a pin means the same thing on either machine.
const ALIASES: &[(&str, &str)] = &[
    ("google chrome", "chrome"),
    ("google chrome canary", "chrome"),
    ("microsoft edge", "msedge"),
    ("brave browser", "brave"),
    ("microsoft teams", "teams"),
    ("zoom.us", "zoom"),
    ("visual studio code", "code"),
];

/// Where `.app` bundles are not things a person would call an app: the system's
/// own agents (Control Center, Siri, the notification server) hold audio
/// clients and would otherwise become mixer columns.
const SYSTEM_AGENTS: &[&str] = &["/System/Library/", "/Library/Apple/"];

/// Where a bare executable is plausibly something the user started (`afplay`,
/// `mpv`, a game) rather than a daemon.
const USER_TOOLS: &[&str] = &["/usr/bin/", "/usr/local/", "/opt/", "/Users/", "/Applications/"];

/// `(key, path of the thing that has the icon)`, or `None` for a process that
/// must not be a column.
pub fn from_path(path: &str) -> Option<(String, String)> {
    // Safari plays through WebKit's GPU service, which lives in the system
    // framework and has no .app of its own. Any WKWebView app lands here too;
    // the process cannot say which one it is working for.
    if path.contains("com.apple.WebKit.GPU") {
        return Some(("safari".into(), "/Applications/Safari.app".into()));
    }
    if SYSTEM_AGENTS.iter().any(|p| path.starts_with(p)) {
        return None;
    }
    if let Some(end) = path.find(".app/") {
        let bundle = &path[..end + 4];
        let name = bundle.rsplit('/').next()?;
        return Some((key_of(name.strip_suffix(".app")?), bundle.to_string()));
    }
    if !USER_TOOLS.iter().any(|p| path.starts_with(p)) {
        return None;
    }
    let base = path.rsplit('/').next().filter(|b| !b.is_empty())?;
    Some((key_of(base), path.to_string()))
}

fn key_of(name: &str) -> String {
    // A comma is the `apps` line's delimiter; nothing else on the wire minds.
    let low = name.to_lowercase().replace(',', " ");
    let low = low.trim();
    ALIASES
        .iter()
        .find(|(from, _)| *from == low)
        .map_or_else(|| low.to_string(), |(_, to)| (*to).to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn key(path: &str) -> Option<String> {
        from_path(path).map(|(k, _)| k)
    }

    #[test]
    fn a_plain_app_is_its_name_lowercased() {
        assert_eq!(
            from_path("/Applications/Spotify.app/Contents/MacOS/Spotify"),
            Some(("spotify".into(), "/Applications/Spotify.app".into()))
        );
    }

    #[test]
    fn a_helper_belongs_to_the_outermost_app() {
        let p = "/Applications/Google Chrome.app/Contents/Frameworks/Google Chrome Framework.framework\
                 /Versions/120/Helpers/Google Chrome Helper (Renderer).app/Contents/MacOS/x";
        assert_eq!(
            from_path(p),
            Some(("chrome".into(), "/Applications/Google Chrome.app".into()))
        );
        let d = "/Applications/Discord.app/Contents/Frameworks/Discord Helper.app/Contents/MacOS/Discord Helper";
        assert_eq!(key(d).as_deref(), Some("discord"));
    }

    #[test]
    fn apple_apps_count_wherever_the_system_keeps_them() {
        assert_eq!(key("/System/Applications/Music.app/Contents/MacOS/Music").as_deref(), Some("music"));
        assert_eq!(
            key("/System/Volumes/Preboot/Cryptexes/App/System/Applications/Safari.app/Contents/MacOS/Safari")
                .as_deref(),
            Some("safari")
        );
    }

    #[test]
    fn safaris_audio_process_is_safari() {
        let p = "/System/Library/Frameworks/WebKit.framework/Versions/A/XPCServices/\
                 com.apple.WebKit.GPU.xpc/Contents/MacOS/com.apple.WebKit.GPU";
        assert_eq!(key(p).as_deref(), Some("safari"));
    }

    #[test]
    fn system_agents_and_daemons_are_never_columns() {
        for p in [
            "/System/Library/CoreServices/ControlCenter.app/Contents/MacOS/ControlCenter",
            "/System/Library/CoreServices/NotificationCenter.app/Contents/MacOS/NotificationCenter",
            "/usr/sbin/coreaudiod",
            "/usr/libexec/avconferenced",
            "",
        ] {
            assert_eq!(key(p), None, "{p}");
        }
    }

    #[test]
    fn a_tool_the_user_started_is_a_column() {
        assert_eq!(key("/usr/bin/afplay").as_deref(), Some("afplay"));
        assert_eq!(key("/opt/homebrew/bin/mpv").as_deref(), Some("mpv"));
    }

    #[test]
    fn user_apps_outside_applications_still_work() {
        assert_eq!(
            key("/Users/me/Applications/Red Alert.app/Contents/MacOS/ra").as_deref(),
            Some("red alert")
        );
    }

    #[test]
    fn a_comma_cannot_split_the_apps_line() {
        assert_eq!(key("/Applications/Foo, Inc.app/Contents/MacOS/foo").as_deref(), Some("foo  inc"));
    }
}
