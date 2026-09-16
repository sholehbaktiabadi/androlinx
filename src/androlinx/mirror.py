"""The mirroring options form, and the command preview underneath it.

The preview is not decoration. Everyone using this app already knows some
scrcpy, and showing the exact command being assembled means the window can be
used to *learn* the flags rather than to hide them -- and when something goes
wrong, the first question ("what did it actually run?") is already answered.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, GObject, Gtk  # noqa: E402

from .const import is_wayland  # noqa: E402
from .models import Profile  # noqa: E402
from .profiles import AUDIO_CODECS, ORIENTATIONS, VIDEO_CODECS  # noqa: E402

#: Offered for --max-size. The value 0 means "no limit".
SIZE_CHOICES = ((0, "Native resolution"), (2048, "2048 px"), (1440, "1440 px"),
                (1080, "1080 px"), (720, "720 px"), (480, "480 px"))

#: Offered for --max-fps. 0 means unlimited.
FPS_CHOICES = ((0, "Unlimited"), (120, "120 fps"), (90, "90 fps"),
               (60, "60 fps"), (30, "30 fps"), (15, "15 fps"))

ORIENTATION_LABELS = ("Automatic", "Portrait", "Landscape (90°)",
                      "Upside down (180°)", "Landscape (270°)")


def _strings(items) -> Gtk.StringList:
    return Gtk.StringList.new(list(items))


def _with_value(choices: tuple, value: int, label: str) -> tuple:
    """Return ``choices``, with ``value`` inserted if it is not already there.

    Profiles may hold any number -- the built-ins use 1024 and 800, and
    profiles.json can be edited by hand. Without this the dropdown would quietly
    snap an unlisted value to the nearest entry it does have, changing the
    user's profile the moment they looked at it.
    """
    if any(candidate == value for candidate, _ in choices):
        return choices
    merged = [*choices, (value, label)]
    # Keep the "no limit" entry first, then descending, as the list is written.
    merged.sort(key=lambda item: (item[0] == 0, -item[0]))
    return tuple(merged)


def _index_of(choices, value, default: int = 0) -> int:
    for index, (candidate, _) in enumerate(choices):
        if candidate == value:
            return index
    return default


class MirrorForm(Gtk.Box):
    """Edits a Profile in place and says when it changed."""

    __gtype_name__ = "AndroLinxMirrorForm"

    __gsignals__ = {
        # The profile's values changed, so the command preview needs rebuilding.
        "changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        self._profile = Profile(name="")
        #: Dropdown entries, which grow to accommodate whatever a profile holds.
        self._size_choices = SIZE_CHOICES
        self._fps_choices = FPS_CHOICES
        #: Set while the widgets are being filled in from a profile, so that
        #: programmatic changes do not look like the user editing.
        self._loading = False

        self._video = self._build_video()
        self._sound = self._build_sound()
        self._window = self._build_window()
        self._device = self._build_device()
        for group in (self._video, self._sound, self._window, self._device):
            self.append(group)

    # -- construction ------------------------------------------------------ #

    def _combo(self, group, title, subtitle, model) -> Adw.ComboRow:
        row = Adw.ComboRow(title=title, subtitle=subtitle, model=model)
        row.connect("notify::selected", self._on_edited)
        group.add(row)
        return row

    def _switch(self, group, title, subtitle) -> Adw.SwitchRow:
        row = Adw.SwitchRow(title=title, subtitle=subtitle)
        row.connect("notify::active", self._on_edited)
        group.add(row)
        return row

    def _build_video(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title="Video")
        self.row_size = self._combo(
            group, "Resolution limit",
            "Smaller is faster and uses less bandwidth",
            _strings(label for _, label in SIZE_CHOICES),
        )
        self.row_bitrate = Adw.SpinRow.new_with_range(1, 100, 1)
        self.row_bitrate.set_title("Bit rate")
        self.row_bitrate.set_subtitle("Megabits per second")
        self.row_bitrate.connect("notify::value", self._on_edited)
        group.add(self.row_bitrate)
        self.row_fps = self._combo(
            group, "Frame rate limit", "", _strings(label for _, label in FPS_CHOICES)
        )
        self.row_codec = self._combo(
            group, "Video codec",
            "H.264 works on every phone; H.265 looks better at the same bit rate",
            _strings(c.upper().replace("H", "H.") for c in VIDEO_CODECS),
        )
        return group

    def _build_sound(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title="Audio and control")
        self.row_audio = self._switch(
            group, "Forward audio", "Play the phone's sound through this computer"
        )
        self.row_audio_codec = self._combo(
            group, "Audio codec", "", _strings(c.upper() for c in AUDIO_CODECS)
        )
        self.row_control = self._switch(
            group, "Allow control",
            "Turn off to mirror without the keyboard and mouse reaching the phone",
        )
        return group

    def _build_window(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title="Window")
        self.row_fullscreen = self._switch(group, "Start fullscreen", "")
        self.row_borderless = self._switch(group, "Hide window decorations", "")
        self.row_on_top = self._switch(group, "Keep above other windows", "")
        self.row_on_top.set_subtitle_lines(3)
        self.set_placement_available(not is_wayland())
        return group

    def set_placement_available(self, available: bool) -> None:
        """Enable or disable the options a Wayland compositor would ignore.

        Wayland gives no application the ability to raise or place its own
        window, so scrcpy silently drops the flag. Explaining that is better
        than offering a switch that quietly does nothing -- and the switch comes
        back the moment X11 compatibility is turned on in Preferences.
        """
        self.row_on_top.set_sensitive(available)
        self.row_on_top.set_subtitle(
            ""
            if available
            else "Not available on Wayland. Turn on X11 compatibility in "
            "Preferences to use it."
        )
        if not available and self.row_on_top.get_active():
            self.row_on_top.set_active(False)

    def _build_device(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title="Phone")
        self.row_screen_off = self._switch(
            group, "Turn the phone's screen off", "The mirror keeps working"
        )
        self.row_awake = self._switch(
            group, "Keep the phone awake", "While it is plugged in"
        )
        self.row_touches = self._switch(
            group, "Show taps on the phone", "Useful when demonstrating something"
        )
        self.row_power_off = self._switch(
            group, "Turn the phone off when mirroring stops", ""
        )
        self.row_orientation = self._combo(
            group, "Orientation", "", _strings(ORIENTATION_LABELS)
        )
        return group

    # -- binding ----------------------------------------------------------- #

    @property
    def profile(self) -> Profile:
        return self._profile

    def load(self, profile: Profile) -> None:
        """Show ``profile``'s values without reporting them as an edit."""
        self._loading = True
        self._profile = profile

        size = profile.max_size or 0
        self._size_choices = _with_value(SIZE_CHOICES, size, f"{size} px")
        self.row_size.set_model(_strings(label for _, label in self._size_choices))
        self.row_size.set_selected(_index_of(self._size_choices, size))

        self.row_bitrate.set_value(profile.video_bit_rate)

        fps = profile.max_fps or 0
        self._fps_choices = _with_value(FPS_CHOICES, fps, f"{fps} fps")
        self.row_fps.set_model(_strings(label for _, label in self._fps_choices))
        self.row_fps.set_selected(_index_of(self._fps_choices, fps))
        self.row_codec.set_selected(
            VIDEO_CODECS.index(profile.video_codec)
            if profile.video_codec in VIDEO_CODECS
            else 0
        )
        self.row_audio.set_active(profile.audio)
        self.row_audio_codec.set_selected(
            AUDIO_CODECS.index(profile.audio_codec)
            if profile.audio_codec in AUDIO_CODECS
            else 0
        )
        self.row_audio_codec.set_sensitive(profile.audio)
        self.row_control.set_active(profile.control)
        self.row_fullscreen.set_active(profile.fullscreen)
        self.row_borderless.set_active(profile.borderless)
        self.row_on_top.set_active(
            profile.always_on_top and self.row_on_top.get_sensitive()
        )
        self.row_screen_off.set_active(profile.turn_screen_off)
        self.row_awake.set_active(profile.stay_awake)
        self.row_touches.set_active(profile.show_touches)
        self.row_power_off.set_active(profile.power_off_on_close)
        self.row_orientation.set_selected(
            ORIENTATIONS.index(profile.orientation)
            if profile.orientation in ORIENTATIONS
            else 0
        )

        self._loading = False

    def _on_edited(self, *_args) -> None:
        if self._loading:
            return
        profile = self._profile
        profile.max_size = self._size_choices[self.row_size.get_selected()][0] or None
        profile.video_bit_rate = int(self.row_bitrate.get_value())
        profile.max_fps = self._fps_choices[self.row_fps.get_selected()][0] or None
        profile.video_codec = VIDEO_CODECS[self.row_codec.get_selected()]
        profile.audio = self.row_audio.get_active()
        profile.audio_codec = AUDIO_CODECS[self.row_audio_codec.get_selected()]
        profile.control = self.row_control.get_active()
        profile.fullscreen = self.row_fullscreen.get_active()
        profile.borderless = self.row_borderless.get_active()
        profile.always_on_top = self.row_on_top.get_active()
        profile.turn_screen_off = self.row_screen_off.get_active()
        profile.stay_awake = self.row_awake.get_active()
        profile.show_touches = self.row_touches.get_active()
        profile.power_off_on_close = self.row_power_off.get_active()
        profile.orientation = ORIENTATIONS[self.row_orientation.get_selected()]

        self.row_audio_codec.set_sensitive(profile.audio)
        self.emit("changed")
