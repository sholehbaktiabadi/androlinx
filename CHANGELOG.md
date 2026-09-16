# Changelog

All notable changes to AndroLinx are recorded here. The Debian packaging keeps
its own history in `debian/changelog`.

## v0.1.0

First release.

- Device sidebar driven by the `adb track-devices` stream, so phones appear and
  disappear without polling. Falls back to polling `adb devices -l` if that
  stream is unavailable, and remains fully usable that way.
- Pixel 9 device frame with a live screen preview. Drawn with GTK's scene graph
  rather than shipped as an image, so it stays sharp at any size and follows the
  light and dark themes.
- Five built-in mirroring profiles — Balanced, Sharp, Smooth, Wireless and
  Presentation — plus profiles of your own.
- A live command preview showing the exact `scrcpy` invocation before it runs.
- Wireless connection and Android 11+ pairing, with mDNS discovery.
- Recording to MP4, stopped with SIGINT first so the file keeps its index and
  stays playable.
- Plain-language diagnostics for the usual adb and scrcpy failures, with a
  suggested remedy for each.
- Options that Wayland cannot honour are disabled and explained, with an opt-in
  XWayland mode for people who want them back.
