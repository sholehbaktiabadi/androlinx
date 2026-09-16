"""Named sets of mirroring options.

The point of the built-ins is that the common answers are already there: most
people want one of "looks good", "reads well", "feels responsive" or "survives
Wi-Fi", and picking between four names is faster than reasoning about bit rates.
They cannot be deleted or edited in place -- editing one offers to save a copy --
so there is always a working configuration to fall back to.
"""

from __future__ import annotations

from dataclasses import fields, replace

from .config import SCHEMA_VERSION, as_choice, as_int, read_json, write_json
from .const import profiles_path
from .models import Profile

#: Codecs scrcpy 3.x accepts. Anything else in a config file is coerced back to
#: h264 rather than passed through to fail at launch.
VIDEO_CODECS = ("h264", "h265", "av1")
AUDIO_CODECS = ("opus", "aac", "flac")

#: scrcpy's --orientation values. Empty means "leave it alone".
ORIENTATIONS = ("", "0", "90", "180", "270")

#: Sensible bounds for the numeric options, applied on load so that a
#: hand-edited file cannot produce a command scrcpy will reject.
MAX_SIZE_RANGE = (240, 4096)
BIT_RATE_RANGE = (1, 100)
FPS_RANGE = (1, 240)


BUILTINS: tuple[Profile, ...] = (
    Profile(
        name="Balanced",
        builtin=True,
        max_size=1080,
        video_bit_rate=8,
        max_fps=60,
        video_codec="h264",
        audio=True,
        stay_awake=True,
    ),
    Profile(
        name="Sharp",
        builtin=True,
        # No size cap: mirror at the phone's own resolution. H.265 carries the
        # extra detail without tripling the bit rate.
        max_size=None,
        video_bit_rate=16,
        max_fps=60,
        video_codec="h265",
        audio=True,
        stay_awake=True,
    ),
    Profile(
        name="Smooth",
        builtin=True,
        # Audio adds latency to the pipeline, so games get it switched off.
        max_size=1024,
        video_bit_rate=6,
        max_fps=60,
        video_codec="h264",
        audio=False,
        stay_awake=True,
    ),
    Profile(
        name="Wireless",
        builtin=True,
        # Sized for a phone on 2.4 GHz Wi-Fi. This is the profile that makes
        # wireless mirroring usable at all.
        max_size=800,
        video_bit_rate=3,
        max_fps=30,
        video_codec="h264",
        audio=False,
        stay_awake=True,
    ),
    Profile(
        name="Presentation",
        builtin=True,
        max_size=1080,
        video_bit_rate=8,
        max_fps=30,
        video_codec="h264",
        audio=False,
        fullscreen=True,
        stay_awake=True,
        # Blanking the phone keeps the audience looking at the projector, and
        # stops a notification preview appearing on the phone in someone's hand.
        turn_screen_off=True,
    ),
)

DEFAULT_PROFILE = BUILTINS[0].name

#: Field names Profile owns, so anything else in the file can be preserved.
_KNOWN = {f.name for f in fields(Profile)} - {"extra"}


def _from_dict(data: dict) -> Profile | None:
    """Build a Profile from one JSON object, coercing anything out of range.

    Returns None when the entry is unusable, in which case the caller skips it
    rather than failing the whole file -- one broken profile must not cost the
    user the other five.
    """
    name = str(data.get("name") or "").strip()
    if not name:
        return None

    max_size = data.get("max_size")
    return Profile(
        name=name,
        builtin=False,
        max_size=(
            None if max_size in (None, 0, "") else as_int(max_size, 1080, *MAX_SIZE_RANGE)
        ),
        video_bit_rate=as_int(data.get("video_bit_rate"), 8, *BIT_RATE_RANGE) or 8,
        max_fps=(
            None
            if data.get("max_fps") in (None, 0, "")
            else as_int(data.get("max_fps"), 60, *FPS_RANGE)
        ),
        video_codec=as_choice(data.get("video_codec"), VIDEO_CODECS, "h264"),
        audio=bool(data.get("audio", True)),
        audio_codec=as_choice(data.get("audio_codec"), AUDIO_CODECS, "opus"),
        fullscreen=bool(data.get("fullscreen", False)),
        always_on_top=bool(data.get("always_on_top", False)),
        borderless=bool(data.get("borderless", False)),
        turn_screen_off=bool(data.get("turn_screen_off", False)),
        stay_awake=bool(data.get("stay_awake", True)),
        show_touches=bool(data.get("show_touches", False)),
        power_off_on_close=bool(data.get("power_off_on_close", False)),
        orientation=as_choice(str(data.get("orientation") or ""), ORIENTATIONS, ""),
        control=bool(data.get("control", True)),
        extra={k: v for k, v in data.items() if k not in _KNOWN},
    )


def _to_dict(profile: Profile) -> dict:
    data = {
        name: getattr(profile, name)
        for name in _KNOWN
        if name != "builtin"
    }
    # Unknown keys from a newer version go back exactly as they arrived.
    data.update(profile.extra)
    return data


def load() -> tuple[list[Profile], bool]:
    """Return (profiles, read_only).

    Built-ins always come first and are never read from disk, so adding one in a
    later release reaches existing users without a migration.
    """
    data, damaged = read_json(profiles_path())
    if data is None:
        return list(BUILTINS), damaged

    version = data.get("version", SCHEMA_VERSION)
    read_only = isinstance(version, int) and version > SCHEMA_VERSION

    user: list[Profile] = []
    taken = {p.name.casefold() for p in BUILTINS}
    for entry in data.get("profiles", []):
        if not isinstance(entry, dict):
            continue
        profile = _from_dict(entry)
        if profile is None or profile.name.casefold() in taken:
            continue
        taken.add(profile.name.casefold())
        user.append(profile)

    return [*BUILTINS, *user], read_only


def save(profiles: list[Profile], *, read_only: bool = False) -> None:
    if read_only:
        return
    write_json(
        profiles_path(),
        {
            "version": SCHEMA_VERSION,
            "profiles": [_to_dict(p) for p in profiles if not p.builtin],
        },
    )


def unique_name(profiles: list[Profile], wanted: str) -> str:
    """A name not already in use, by appending a number if need be."""
    wanted = wanted.strip() or "Custom"
    taken = {p.name.casefold() for p in profiles}
    if wanted.casefold() not in taken:
        return wanted
    for n in range(2, 100):
        candidate = f"{wanted} {n}"
        if candidate.casefold() not in taken:
            return candidate
    return f"{wanted} {len(profiles)}"


def find(profiles: list[Profile], name: str) -> Profile:
    """Look a profile up by name, falling back to the first one."""
    for profile in profiles:
        if profile.name == name:
            return profile
    return profiles[0] if profiles else replace(BUILTINS[0])
