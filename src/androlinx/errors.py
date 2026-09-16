"""Turning adb and scrcpy's output into something a person can act on.

Both tools write good diagnostics, but they write them for someone who already
knows the tool. ``error: device unauthorized`` is precise and tells a newcomer
nothing about what to do. Every rule here exists because the raw text names a
real, fixable situation, and the fix is worth spelling out.

The rules are ordered and the first match wins, so specific patterns must come
before general ones -- ``device offline (transport offline)`` has to be matched
before a looser rule for ``offline`` can shadow it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: Where the text came from. The same words can mean different things depending
#: on which tool said them, and the fallback differs too.
SOURCE_ADB = "adb"
SOURCE_SCRCPY = "scrcpy"

# --------------------------------------------------------------------------- #
# Exceptions
# --------------------------------------------------------------------------- #


class AndroLinxError(RuntimeError):
    """Base class, so callers can catch everything this package raises."""


class AdbError(AndroLinxError):
    pass


class ScrcpyError(AndroLinxError):
    pass


class TrackFormatError(AdbError):
    """The track-devices stream did not match adb's framing.

    Raised rather than guessed at: losing sync with a length-prefixed protocol
    means every later frame is garbage, so it is better to abandon streaming and
    fall back to polling.
    """


class MissingToolError(AndroLinxError):
    """A required binary is not installed.

    Carries the apt package name so the UI can show the exact install command
    rather than a generic "not found".
    """

    def __init__(self, binary: str, package: str):
        super().__init__(f"{binary} is not installed")
        self.binary = binary
        self.package = package


# --------------------------------------------------------------------------- #
# Remedies the UI may offer as a button
# --------------------------------------------------------------------------- #

ACTION_RETRY = "retry"
ACTION_RECONNECT = "reconnect"
ACTION_RESTART_ADB = "restart-adb"
ACTION_AUTHORISE = "authorise"
ACTION_UDEV_HELP = "udev-help"
ACTION_PAIR_AGAIN = "pair-again"
ACTION_INSTALL_SCRCPY = "install-scrcpy"
ACTION_OPEN_FOLDER = "open-folder"

#: Every action a rule is allowed to name. The test suite asserts the rules only
#: ever use one of these, so the UI can switch on them exhaustively.
ACTIONS = (
    ACTION_RETRY,
    ACTION_RECONNECT,
    ACTION_RESTART_ADB,
    ACTION_AUTHORISE,
    ACTION_UDEV_HELP,
    ACTION_PAIR_AGAIN,
    ACTION_INSTALL_SCRCPY,
    ACTION_OPEN_FOLDER,
)


@dataclass(frozen=True)
class Diagnosis:
    """What went wrong, what to do about it, and the text it came from."""

    #: One short sentence in the user's terms. Goes in the toast or banner title.
    title: str
    #: What to do next. Empty when there is honestly nothing useful to add.
    detail: str = ""
    #: One of ACTIONS, or None when no button makes sense.
    action: str | None = None
    #: The original text, kept so the UI can offer "Copy details".
    raw: str = ""
    #: True when the message turned out not to be a failure at all.
    ok: bool = False


#: (pattern, title, detail, action). Order matters; first match wins.
_RULES: tuple[tuple[str, str, str, str | None], ...] = (
    # -- not actually failures ------------------------------------------- #
    (
        r"already connected to",
        "Already connected",
        "",
        None,
    ),
    (
        r"[Ss]uccessfully paired",
        "Paired successfully",
        "",
        None,
    ),
    # -- authorisation and permissions ------------------------------------ #
    (
        r"(device unauthorized|Device is unauthorized)",
        "This phone has not authorised this computer",
        "Unlock the phone and tap Allow on the “Allow USB debugging?” prompt. "
        "Tick “Always allow from this computer” so it does not ask again.",
        ACTION_AUTHORISE,
    ),
    (
        r"no permissions",
        "Linux is not allowing access to this USB device",
        "The udev rules are missing. Install android-sdk-platform-tools-common, "
        "make sure your user is in the plugdev group, then unplug and replug the cable.",
        ACTION_UDEV_HELP,
    ),
    # -- connection state -------------------------------------------------- #
    (
        r"device offline",
        "The phone stopped responding to adb",
        "This usually clears by itself. If it does not, unplug and replug the "
        "cable, or turn USB debugging off and on again.",
        ACTION_RECONNECT,
    ),
    (
        r"error: closed",
        "The phone disconnected while adb was talking to it",
        "Check the cable and try again.",
        ACTION_RETRY,
    ),
    (
        r"protocol fault",
        "The adb connection broke mid-command",
        "Restarting the adb server usually fixes this.",
        ACTION_RESTART_ADB,
    ),
    # -- the adb server itself --------------------------------------------- #
    (
        r"adb server version .* doesn't match",
        "Another program is running a different version of adb",
        "Android Studio, Genymotion or Waydroid has its own adb and took over the "
        "server. Close it, or restart the adb server to take it back.",
        ACTION_RESTART_ADB,
    ),
    (
        r"(cannot bind listener|Address already in use)",
        "Port 5037 is already in use",
        "Something else is listening on the adb port. "
        "Check with: ss -ltnp 'sport = :5037'",
        ACTION_RESTART_ADB,
    ),
    (
        r"(cannot connect to daemon|failed to start daemon|Could not start adb server)",
        "The adb server could not start",
        "Try restarting it. If that fails, another adb may be holding port 5037.",
        ACTION_RESTART_ADB,
    ),
    (
        r"Could not list ADB devices",
        "adb is not working",
        "Restart the adb server and try again.",
        ACTION_RESTART_ADB,
    ),
    # -- nothing to talk to ------------------------------------------------ #
    (
        r"(no devices/emulators found|Could not find any ADB device)",
        "No device is connected",
        "Check that USB debugging is on, and that the cable carries data — "
        "many charging cables do not.",
        ACTION_RETRY,
    ),
    (
        r"Could not find ADB device",
        "That phone is no longer connected",
        "It disconnected between being listed and being started.",
        ACTION_RETRY,
    ),
    (
        r"(more than one device|Multiple \(\d+\) ADB devices)",
        "More than one device is connected",
        "Pick which one to use in the sidebar.",
        None,
    ),
    # -- wireless ---------------------------------------------------------- #
    (
        r"Connection refused",
        "The phone refused the connection",
        "Wireless debugging may be switched off, or the port has changed. "
        "Android picks a new port every time it is toggled.",
        ACTION_PAIR_AGAIN,
    ),
    (
        r"(No route to host|Network is unreachable)",
        "That address cannot be reached",
        "Check that the phone and this computer are on the same network, and that "
        "the router does not isolate wireless clients from each other.",
        ACTION_RETRY,
    ),
    (
        r"(Operation timed out|timed out)",
        "The phone did not answer",
        "Wake the screen and try again. Some phones only answer while the "
        "Wireless debugging screen is open.",
        ACTION_RETRY,
    ),
    (
        r"Wrong password or connection was dropped",
        "That pairing code was not accepted",
        "Pairing codes expire after a couple of minutes. Open Wireless debugging "
        "on the phone, tap “Pair device with pairing code”, and use the new code.",
        ACTION_PAIR_AGAIN,
    ),
    (
        r"[Uu]nable to (start|create) pairing client",
        "Could not reach the pairing port",
        "The pairing port is not the same as the connection port. Use the address "
        "and port shown under “Pair device with pairing code”.",
        ACTION_PAIR_AGAIN,
    ),
    (
        r"failed to authenticate",
        "The phone rejected the pairing",
        "Pair again with a fresh code.",
        ACTION_PAIR_AGAIN,
    ),
    (
        r"Failed to parse address",
        "That does not look like an address",
        "Expected something like 192.168.1.5:5555.",
        None,
    ),
    # -- scrcpy: getting its server onto the phone ------------------------- #
    (
        r"Failed to push",
        "Could not copy scrcpy's helper onto the phone",
        "/data/local/tmp may be full, or a work profile or device-management "
        "policy is blocking it.",
        ACTION_RETRY,
    ),
    (
        r"Server connection failed",
        "The phone took scrcpy's helper but never called back",
        "An always-on VPN on the phone blocks the loopback tunnel. Turn it off, "
        "or exclude adb from it.",
        ACTION_RETRY,
    ),
    # -- scrcpy: encoding -------------------------------------------------- #
    (
        r"(Could not open codec|Raw video encoder not found|Could not fill codec context|Encoder .* not found)",
        "This phone's encoder refused those settings",
        "Lower the bit rate, or switch the video codec to H.264 — it is the one "
        "every phone can encode in hardware.",
        None,
    ),
    # -- scrcpy: window ---------------------------------------------------- #
    (
        r"(Could not initialize SDL|Could not create window|Could not create renderer)",
        "scrcpy could not open a window",
        "If X11 compatibility is turned on in Preferences, try turning it off — "
        "or on, if it is currently off.",
        None,
    ),
    # -- scrcpy: recording ------------------------------------------------- #
    (
        r"(Could not find muxer|Failed to write header)",
        "Could not start the recording",
        "The file format does not accept that codec. MP4 with H.264 always works.",
        None,
    ),
    (
        r"Recording failed",
        "The recording could not be written",
        "The disk may be full, or the folder is not writable.",
        ACTION_OPEN_FOLDER,
    ),
    (
        r"Failed to write trailer",
        "The video file was left incomplete",
        "scrcpy was killed rather than stopped cleanly, so the file has no index. "
        "Stop recordings with the Stop button to avoid this.",
        ACTION_OPEN_FOLDER,
    ),
    # -- missing tools ----------------------------------------------------- #
    (
        r"(Command not found|Failed to execute)",
        "A required program is missing",
        "Install it with: sudo apt install adb scrcpy",
        ACTION_INSTALL_SCRCPY,
    ),
    (
        r"Device disconnected",
        "The phone disconnected",
        "Reconnect it and try again.",
        ACTION_RETRY,
    ),
)

_COMPILED = tuple((re.compile(p), t, d, a) for p, t, d, a in _RULES)

#: Lines scrcpy writes constantly that never explain a failure.
_NOISE = re.compile(r"^\s*(INFO|DEBUG|VERBOSE|TRACE)\s*:")

#: Messages that mean success even though they arrived on stderr.
_OK = re.compile(r"(already connected to|[Ss]uccessfully paired)")


def _last_meaningful_line(raw: str) -> str:
    """The last line that actually says something.

    scrcpy prints twenty INFO lines before the one that matters, and adb's
    interesting line is usually the only one. Walking backwards past the noise
    finds the right line in both cases.
    """
    for line in reversed(raw.replace("\r\n", "\n").replace("\r", "\n").splitlines()):
        stripped = line.strip()
        if stripped and not _NOISE.match(stripped):
            return stripped
    return ""


def map_error(raw: str, *, source: str = SOURCE_ADB) -> Diagnosis:
    """Translate raw tool output into something worth showing a person."""
    text = (raw or "").strip()
    if not text:
        return Diagnosis(
            title="Something went wrong",
            detail="The command failed without saying why.",
            action=ACTION_RETRY,
            raw=raw or "",
        )

    for pattern, title, detail, action in _COMPILED:
        if pattern.search(text):
            return Diagnosis(
                title=title,
                detail=detail,
                action=action,
                raw=text,
                ok=bool(_OK.search(text)),
            )

    # Nothing matched. Show the most informative line rather than the whole dump,
    # and strip the "error: " prefix adb puts on everything.
    line = _last_meaningful_line(text) or text
    line = re.sub(r"^(ERROR|error)\s*:\s*", "", line).strip()
    tool = "scrcpy" if source == SOURCE_SCRCPY else "adb"
    return Diagnosis(
        title=line[:120] if line else f"{tool} failed",
        detail=f"Reported by {tool}.",
        action=ACTION_RETRY,
        raw=text,
    )
