# AndroLinx

A desktop application for mirroring and controlling an Android phone from
Ubuntu. It puts a GTK4 interface on `adb` and `scrcpy`, the two tools that
already do this job well from a terminal.

Phones appear and disappear in the sidebar on their own. Mirroring options live
in named profiles instead of in your shell history. Connecting over Wi-Fi is a
dialog rather than three commands typed in the right order. And a phone frame
shows a live preview of the selected device, so you can see which one you are
about to mirror before you start.

## What it does not do

**AndroLinx does not replace scrcpy's window, and cannot wrap one.** scrcpy
draws into its own SDL window; GTK4 removed the API for embedding a foreign
window, and Wayland does not let one application reparent another's surface at
all. So the mirror opens as its own window, exactly as it does today.

What AndroLinx does is everything around that: find the devices, keep the
options, build the command, run it, and tell you in plain words when something
goes wrong. The live preview inside the phone frame is a periodic screenshot,
not the mirror.

If you want the mirror embedded in a single window, no tool on Linux can do that
today without reimplementing scrcpy, and this is not that project.

## Installing

```bash
sudo apt install ./androlinx_0.1.0_all.deb
```

Building from source:

```bash
sudo apt install debhelper dh-python pybuild-plugin-pyproject python3-all python3-setuptools
dpkg-buildpackage -us -uc -b
sudo apt install ../androlinx_0.1.0_all.deb
```

If debhelper is not available on that machine, `tools/build-deb.sh` produces a
package with the same contents without needing any extra build packages:

```bash
./tools/build-deb.sh
sudo apt install ./androlinx_0.1.0_all.deb
```

Running from the source tree, without installing anything:

```bash
PYTHONPATH=src python3 -m androlinx
```

## Using it

Open **AndroLinx** from your application menu. Connect a phone over USB with
USB debugging turned on, and it appears in the sidebar within a moment. Pick a
profile, press **Start mirroring**, and scrcpy opens.

The command being assembled is shown at the bottom of the options, so you can
see exactly what is going to run — and copy it if you would rather run it
yourself next time.

From a terminal:

```bash
androlinx            # open the window
androlinx --list     # list connected devices and exit
androlinx --version
```

### Going wireless

Press the Wi-Fi button in the sidebar. Phones advertising wireless debugging on
your network are listed automatically.

For a phone that has never been paired with this computer, on the phone open
**Developer options → Wireless debugging → Pair device with pairing code**, then
use the **Pair a new device** section. Note that the pairing port shown on that
screen is *not* the port you connect to afterwards — Android uses two, and
mixing them up is the single most common reason this fails by hand. AndroLinx
keeps the two steps separate for exactly that reason.

### Profiles

Five come built in:

| Profile | For | Roughly |
|---|---|---|
| **Balanced** | Everyday use | 1080 px, 8 Mbps, 60 fps, H.264, audio on |
| **Sharp** | Reading text, showing detail | Native resolution, 16 Mbps, H.265 |
| **Smooth** | Games | 1024 px, 6 Mbps, 60 fps, audio off |
| **Wireless** | A phone on 2.4 GHz Wi-Fi | 800 px, 3 Mbps, 30 fps, audio off |
| **Presentation** | A projector | 1080 px, fullscreen, phone screen blanked |

Change anything in the form and press the save button beside the profile name to
keep it as one of your own. Built-in profiles are never modified, so there is
always something working to go back to.

Your profiles live in `~/.config/androlinx/profiles.json` and are plain JSON, so
they can be edited by hand or copied between machines.

## How it works

```
  AndroLinx (GTK4)
        |
        |  adb track-devices -l      streamed, so the sidebar updates
        |--------------------------> the instant a cable is plugged in
        |
        |  adb shell (one round trip) model, Android version,
        |--------------------------> resolution, battery
        |
        |  adb exec-out screencap -p  the live preview inside the
        |--------------------------> phone frame, a frame every 2s
        |
        |  scrcpy -s ... --max-size=...
        '--------------------------> opens its own window
                                     AndroLinx watches it and can stop it
```

Device discovery uses `adb track-devices`, which streams the whole device list
every time it changes rather than being polled. If that stream is unavailable —
it is an undocumented command and could change — AndroLinx falls back to polling
`adb devices -l` and says so in a banner. Every feature works either way.

Device details come from a single batched `adb shell` invocation rather than
four separate ones, so selecting a phone costs one round trip, not four.

### Recording

Recording is the same as mirroring with `--record` added. Stopping a recording
sends `SIGINT` first and waits several seconds, because scrcpy writes the MP4
index when it shuts down cleanly — a killed recording produces a file no player
will open. `SIGKILL` is only ever used as a last resort, and a recording that
produced a zero-byte file is reported as a failure rather than left lying around.

If a recording is running when you close AndroLinx, it is always stopped
properly first, whatever your "when AndroLinx closes" setting says.

## Wayland

Ubuntu 26.04 uses Wayland by default, and a Wayland compositor does not let an
application place or raise another program's window. scrcpy's `--always-on-top`
and window position options therefore have no effect, so AndroLinx disables
those switches and says why rather than offering something that silently does
nothing.

Turning on **X11 compatibility** in Preferences runs scrcpy through XWayland,
where they work again. The cost is that text can look softer on a HiDPI screen
and mouse capture behaves a little differently, which is why it is off by
default rather than on.

## Requirements

- Ubuntu 25.04 or newer, or another distribution with GTK 4.12+ and libadwaita 1.7+
  (`Adw.Spinner` arrived in 1.7 and `Gtk.ListBox.remove_all` in 4.12; both are
  declared in the package dependencies, so apt will not let it install where it
  would not run)
- `adb` and `scrcpy` 3.0 or newer (both are `Depends` of the package)
- A phone with USB debugging turned on, and a cable that carries data

scrcpy 2.x is not supported: several of the options AndroLinx emits were renamed
or introduced in 3.0, and supporting both spellings is not worth the bugs.

## Tested on

| Component | Version |
|---|---|
| Ubuntu | 26.04 LTS (Resolute Raccoon) |
| GNOME Shell | 50.1, Wayland |
| GTK | 4.22.4 |
| libadwaita | 1.9.1 |
| Python | 3.14.4 |
| PyGObject | 3.56.2 |
| adb | 34.0.5 |
| scrcpy | 3.3.4 |

### Not yet tested

- X11 sessions. The Wayland-specific options are disabled by detecting
  `XDG_SESSION_TYPE`, and on X11 they should simply be available, but that path
  has not been exercised on a real X11 session.
- Devices in `bootloader`, `recovery` or `sideload` state. They are listed and
  marked as not mirrorable, which is all that is claimed.
- Tablets and foldables. The phone frame letterboxes anything that is not 20:9
  rather than distorting it, so they should look correct, just framed by a phone.
- Connecting through `adb -H` to a remote adb server.

## Development

```bash
python3 -m unittest discover -s tests -v    # 149 tests, no device needed
PYTHONPATH=src python3 -m androlinx         # run from the source tree
```

The test suite covers the parts worth testing: the adb output parsers, the
`track-devices` frame reader, the scrcpy command builder, the profile and
settings stores, and the error messages. None of it imports `gi`, spawns a
process or needs a display, so it runs anywhere — including in the package build
chroot, where `debian/rules` runs it on every build.

The source layout is flat on purpose: `tools/build-deb.sh` stages the package
with a `src/androlinx/*.py` glob, so adding a subdirectory there would silently
drop files from the fallback package. CI compares the contents of the two builds
against each other to catch exactly that.

## License

GPL-3.0-or-later. See [LICENSE](LICENSE).

scrcpy and adb are separate programs with their own licenses; AndroLinx runs
them, and does not include or modify either.
