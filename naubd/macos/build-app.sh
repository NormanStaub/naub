#!/bin/sh
# Build target/naub.app: what "Start at Login" and a grant in naub's own name need.
#
#   macos/build-app.sh                  ad-hoc signed, for this Mac
#   NAUB_SIGN_ID="Apple Development: Me (TEAMID)" macos/build-app.sh
#
# Reports say macOS can silently refuse capture to ad-hoc apps with no Team ID.
# If the app reads no audio but `naubd --doctor` from Terminal does, that is the
# first suspect: sign with any Apple ID's free Development certificate
# (`security find-identity -v -p codesigning` lists what you have).
set -eu
cd "$(dirname "$0")/.."

cargo build --release
app=target/naub.app
rm -rf "$app"
mkdir -p "$app/Contents/MacOS"
cp target/release/naubd "$app/Contents/MacOS/naubd"
cp macos/Info.plist "$app/Contents/Info.plist"
codesign --force --timestamp=none --sign "${NAUB_SIGN_ID:--}" \
    --identifier com.normanstaub.naub "$app"
codesign --verify --verbose=1 "$app"
echo "built $(pwd)/$app"
echo "run it with: open $app"
