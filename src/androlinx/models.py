"""The three records the whole application is built around.

``Device`` is what adb tells us, ``Profile`` is what the user configured, and
``Session`` is a scrcpy process that is running right now. They carry no
behaviour beyond presentation helpers so that they can be constructed in tests
without adb, scrcpy or a display.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path

#: adb reports these in the second column of ``adb devices``. Anything else is
#: passed through untouched so a future adb cannot break the parser.
STATE_DEVICE = "device"
STATE_UNAUTHORIZED = "unauthorized"
STATE_OFFLINE = "offline"
STATE_NO_PERMISSIONS = "no permissions"

#: Only a device in this state can be mirrored.
READY_STATES = (STATE_DEVICE,)


@dataclass
class Device:
    """One entry from ``adb devices -l``, optionally enriched from the phone."""

    #: Serial number, or ``host:port`` for a device attached over TCP/IP.
    serial: str
    #: Raw adb state. Compare against the STATE_* constants above.
    state: str = STATE_DEVICE
    #: ``model:`` from ``adb devices -l``; underscores, e.g. ``Pixel_9``.
    model: str | None = None
    #: ``product:`` and ``device:`` from the same line. Kept for disambiguating
    #: two phones of the same model.
    product: str | None = None
    codename: str | None = None
    #: ``transport_id:``. Unique per connection, unlike the serial, which is
    #: shared between a phone's USB and TCP/IP entries.
    transport_id: str | None = None

    # -- filled in by the second, slower enrichment pass ------------------- #
    manufacturer: str | None = None
    #: Android release as shown in Settings, e.g. ``"15"``.
    android_release: str | None = None
    #: Physical display size in pixels, as reported by ``wm size``.
    width: int | None = None
    height: int | None = None
    #: Battery percentage, or None when the phone did not report one.
    battery: int | None = None
    #: True once the enrichment pass has run, so the UI knows the blanks are
    #: real rather than merely not fetched yet.
    enriched: bool = False

    @property
    def is_emulator(self) -> bool:
        return self.serial.startswith("emulator-")

    @property
    def over_tcpip(self) -> bool:
        """True when this device is attached over the network rather than USB.

        adb writes network devices two different ways. The ordinary one is
        ``host:port``. The other appears when adb auto-connects through mDNS,
        and looks like ``adb-R58M99-xyz._adb-tls-connect._tcp`` -- no port at
        all, so checking for a colon is not enough.
        """
        if self.serial.endswith("._tcp"):
            return True
        host, sep, port = self.serial.rpartition(":")
        return bool(sep) and host != "" and port.isdigit()

    @property
    def ready(self) -> bool:
        return self.state in READY_STATES

    @property
    def display_name(self) -> str:
        """A human name for the sidebar, never empty."""
        if self.model:
            name = self.model.replace("_", " ").strip()
            if name:
                maker = (self.manufacturer or "").strip()
                # getprop reports these lower case ("samsung"), so fix the first
                # letter only -- .title() would turn "OnePlus" into "Oneplus".
                maker = maker[:1].upper() + maker[1:]
                # "Samsung SM-A525F" reads better than "SM-A525F", but "Google
                # Pixel 9" is redundant when the model already says Pixel.
                if maker and not name.lower().startswith(maker.lower()):
                    return f"{maker} {name}"
                return name
        return self.serial

    @property
    def transport_label(self) -> str:
        if self.is_emulator:
            return "Emulator"
        return "Wi-Fi" if self.over_tcpip else "USB"

    @property
    def resolution(self) -> str | None:
        if self.width and self.height:
            return f"{self.width}×{self.height}"
        return None

    def summary(self) -> str:
        """The subtitle under the device name: transport, Android, battery."""
        parts = [self.transport_label]
        if self.android_release:
            parts.append(f"Android {self.android_release}")
        if self.battery is not None:
            parts.append(f"{self.battery}%")
        return " · ".join(parts)

    def merged_with(self, other: Device) -> Device:
        """Return ``other`` with this device's enrichment carried over.

        The tracker re-reads ``adb devices -l`` on every change, which knows
        nothing about Android version or battery. Without this, a second phone
        being plugged in would blank the details of the first.
        """
        if other.enriched or self.serial != other.serial:
            return other
        return replace(
            other,
            manufacturer=self.manufacturer,
            android_release=self.android_release,
            width=self.width,
            height=self.height,
            battery=self.battery,
            enriched=self.enriched,
        )


@dataclass
class Profile:
    """A named set of scrcpy options.

    Field names deliberately echo the scrcpy option they produce, so that
    ``scrcpy.build_command`` reads as a straight translation and the command
    preview in the UI is easy to check by eye.
    """

    name: str
    #: True for the profiles AndroLinx ships. They can be copied but not edited
    #: away, so there is always something sensible to fall back to.
    builtin: bool = False

    # -- video ------------------------------------------------------------- #
    #: Longest edge in pixels; None leaves the device resolution alone.
    max_size: int | None = 1080
    #: Video bit rate in megabits per second.
    video_bit_rate: int = 8
    max_fps: int | None = 60
    video_codec: str = "h264"

    # -- audio ------------------------------------------------------------- #
    audio: bool = True
    audio_codec: str = "opus"

    # -- window ------------------------------------------------------------ #
    fullscreen: bool = False
    always_on_top: bool = False
    borderless: bool = False

    # -- device ------------------------------------------------------------ #
    turn_screen_off: bool = False
    stay_awake: bool = True
    show_touches: bool = False
    power_off_on_close: bool = False
    #: One of scrcpy's orientation values: 0, 90, 180, 270 (as strings) or "".
    orientation: str = ""

    # -- control ----------------------------------------------------------- #
    #: False passes --no-control, making the mirror view-only.
    control: bool = True

    #: Unrecognised keys found in profiles.json, kept so that a file written by a
    #: newer AndroLinx survives a round trip through an older one.
    extra: dict = field(default_factory=dict, repr=False)

    def copy_as(self, name: str) -> Profile:
        return replace(self, name=name, builtin=False)


@dataclass
class Session:
    """A scrcpy process AndroLinx started and is still watching."""

    serial: str
    #: Name of the profile it was launched with, for the sidebar subtitle.
    profile_name: str
    pid: int
    #: ``time.monotonic()`` at launch, used for the elapsed-time label.
    started_at: float
    #: Set when this session is recording; None for plain mirroring.
    record_path: Path | None = None
    #: Device name at launch time, so the row stays readable after unplugging.
    device_name: str = ""

    @property
    def recording(self) -> bool:
        return self.record_path is not None
