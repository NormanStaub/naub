//! On a Mac, embed Info.plist in the binary itself.
//!
//! A process with no NSAudioCaptureUsageDescription does not get refused audio
//! capture: it gets `noErr` and silence, indefinitely. Embedding the plist means
//! a bare `naubd`, not only one inside naub.app, carries the string.

fn main() {
    println!("cargo:rerun-if-changed=macos/Info.plist");
    if std::env::var("CARGO_CFG_TARGET_OS").as_deref() != Ok("macos") {
        return;
    }
    let dir = std::env::var("CARGO_MANIFEST_DIR").expect("cargo sets this");
    println!("cargo:rustc-link-arg-bins=-Wl,-sectcreate,__TEXT,__info_plist,{dir}/macos/Info.plist");
}
