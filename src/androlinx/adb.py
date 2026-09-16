"""A thin layer over the adb command line tool.

There are no official Python bindings for adb, so everything goes through the
``adb`` binary the same way every other adb front-end does. The parsers are kept
separate from the calls and take plain strings, which is what lets the whole
suite of parser tests run in a build chroot with no phone and no adb installed.

Two things about adb's output bite every parser written against it, and both are
handled here deliberately:

* ``adb shell`` allocates a pty by default and the pty translates LF into CRLF.
  Every call passes ``-T`` to switch that off, and every parser normalises line
  endings anyway, because ``exec-out`` and older adb versions differ.
* ``adb`` writes ``* daemon not running; starting now at tcp:5037`` to stdout,
  in the middle of the output you asked for. Lines starting with ``*`` are
  skipped everywhere.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import replace

from .const import ADB_TIMEOUT, DEFAULT_ADB_PORT, SCREENCAP_TIMEOUT
from .errors import AdbError, MissingToolError, TrackFormatError
from .models import Device

#: The Ubuntu package that provides /usr/bin/adb.
ADB_PACKAGE = "adb"

#: One round trip collects everything the device page shows. Sections are marked
#: with sentinels rather than parsed positionally throughout, because ROMs vary
#: in how many lines each command produces -- but the properties inside @P *are*
#: positional, so a missing property still occupies its line.
#:
#: The trailing @E is how we detect truncation: if the phone is unplugged
#: mid-probe we get a partial answer, and half a result is worse than none.
PROBE_SCRIPT = (
    "echo @P;"
    " getprop ro.product.manufacturer;"
    " getprop ro.product.model;"
    " getprop ro.build.version.release;"
    " getprop ro.build.version.sdk;"
    " echo @S; wm size 2>/dev/null;"
    " echo @D; wm density 2>/dev/null;"
    " echo @B; dumpsys battery 2>/dev/null;"
    " echo @E"
)


def _normalise(text: str) -> str:
    """Collapse CRLF and lone CR. Called by every parser, without exception."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def available() -> bool:
    return shutil.which("adb") is not None


def require() -> None:
    if not available():
        raise MissingToolError("adb", ADB_PACKAGE)


def _run(
    args: list[str],
    *,
    check: bool = True,
    timeout: float = ADB_TIMEOUT,
    binary: bool = False,
) -> str | bytes:
    """Run adb and return its stdout.

    ``binary`` matters for screencap, whose output is a PNG and must never go
    near a text decoder.
    """
    argv = ["adb", *args]
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=not binary,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise MissingToolError("adb", ADB_PACKAGE) from exc
    except subprocess.TimeoutExpired as exc:
        raise AdbError(f"adb did not respond within {timeout:g}s") from exc

    if check and proc.returncode != 0:
        if binary:
            detail = (proc.stderr or b"").decode("utf-8", "replace").strip()
        else:
            detail = (proc.stderr or proc.stdout or "").strip()
        raise AdbError(detail or f"adb {' '.join(args)} failed")
    return proc.stdout


def _text(args: list[str], **kwargs) -> str:
    result = _run(args, **kwargs)
    assert isinstance(result, str)
    return result


# --------------------------------------------------------------------------- #
# Parsing `adb devices -l`
# --------------------------------------------------------------------------- #

#: adb's full state vocabulary. Anything outside it is still listed, just not
#: classified -- dropping a row we cannot label would hide a real device.
KNOWN_STATES = (
    "device",
    "offline",
    "unauthorized",
    "authorizing",
    "connecting",
    "no permissions",
    "bootloader",
    "recovery",
    "rescue",
    "sideload",
    "detached",
    "host",
)

#: `no permissions` is the only two-word state, and it is followed by
#: `; see [http://...]`. That URL contains a colon, so it must be removed before
#: the key:value attributes are parsed or `http` becomes a bogus attribute.
_NO_PERMISSIONS = re.compile(r"no permissions\s*(;\s*see\s*\[[^\]]*\])?")


def parse_devices(text: str) -> list[Device]:
    """Parse ``adb devices`` or ``adb devices -l`` into Device records.

    Also parses a ``track-devices`` payload, which is the same body without the
    header line.
    """
    devices: list[Device] = []
    for line in _normalise(text).splitlines():
        line = line.strip()
        if not line or line.startswith("*"):
            # "* daemon not running; starting now at tcp:5037" and friends.
            continue
        if line.startswith("List of devices"):
            continue

        serial, _, rest = line.partition("\t")
        if not _:
            # Older adb and the short track-devices form use spaces, not tabs.
            serial, _, rest = line.partition(" ")
        serial = serial.strip()
        rest = rest.strip()
        if not serial:
            continue

        match = _NO_PERMISSIONS.search(rest)
        if match:
            state = "no permissions"
            rest = rest[: match.start()] + rest[match.end() :]
        else:
            state, _, rest = rest.partition(" ")
            state = state.strip()

        attrs: dict[str, str] = {}
        for token in rest.split():
            key, sep, value = token.partition(":")
            if sep and key:
                attrs.setdefault(key, value)

        model = attrs.get("model")
        devices.append(
            Device(
                serial=serial,
                state=state or "unknown",
                model=model,
                product=attrs.get("product"),
                codename=attrs.get("device"),
                transport_id=attrs.get("transport_id"),
            )
        )
    return devices


# --------------------------------------------------------------------------- #
# Parsing the device probe
# --------------------------------------------------------------------------- #

#: `Override size` is what scrcpy actually captures when a display override is
#: set, so it wins over `Physical size` when both are present.
_SIZE = re.compile(r"(Physical|Override) size:\s*(\d+)\s*x\s*(\d+)")
_LEVEL = re.compile(r"^\s*level:\s*(\d+)\s*$", re.MULTILINE)


def parse_probe(text: str) -> dict:
    """Parse the sentinel-delimited output of PROBE_SCRIPT.

    Raises AdbError when the closing sentinel is missing, which means the phone
    went away mid-probe and the answer is incomplete.
    """
    body = _normalise(text)
    if "@E" not in body:
        raise AdbError("the device stopped responding while being probed")

    sections: dict[str, list[str]] = {}
    current = ""
    for line in body.splitlines():
        stripped = line.strip()
        if stripped in ("@P", "@S", "@D", "@B", "@E"):
            current = stripped
            sections.setdefault(current, [])
            continue
        if current:
            sections.setdefault(current, []).append(line)

    out: dict = {}

    # @P is positional: manufacturer, model, release, sdk -- one line each, in
    # that order. getprop prints an empty line for a property that is not set,
    # so the positions hold even when a value is missing.
    props = [ln.strip() for ln in sections.get("@P", [])]
    props += [""] * (4 - len(props))
    for key, value in zip(("manufacturer", "model", "android_release", "sdk"), props):
        out[key] = value or None
    if out.get("sdk"):
        try:
            out["sdk"] = int(out["sdk"])
        except ValueError:
            out["sdk"] = None

    sizes = dict(
        (kind, (int(w), int(h)))
        for kind, w, h in _SIZE.findall("\n".join(sections.get("@S", [])))
    )
    size = sizes.get("Override") or sizes.get("Physical")
    out["width"], out["height"] = size if size else (None, None)

    density = re.search(
        r"(Physical|Override) density:\s*(\d+)", "\n".join(sections.get("@D", []))
    )
    out["density"] = int(density.group(2)) if density else None

    battery_text = "\n".join(sections.get("@B", []))
    level = _LEVEL.search(battery_text)
    out["battery"] = int(level.group(1)) if level else None

    return out


# --------------------------------------------------------------------------- #
# Parsing the `adb track-devices` stream
# --------------------------------------------------------------------------- #

#: A real device list is a few hundred bytes; a hundred devices would still be
#: under 16 KiB. Anything larger means we have lost sync with the framing and
#: are reading payload as though it were a length. The bound has to sit below
#: 0xFFFF to be reachable at all, since that is the largest length four
#: hexadecimal characters can express.
MAX_FRAME = 16 * 1024


class TrackStream:
    """Incremental parser for the framed ``adb track-devices`` byte stream.

    Pure: it is fed bytes and returns snapshots. Kept separate from the thread
    that reads the pipe so that the awkward cases -- a frame split across reads,
    several frames in one read, a multi-byte character straddling the boundary
    -- can be tested without a device.
    """

    def __init__(self) -> None:
        self._buffer = bytearray()

    def feed(self, chunk: bytes) -> list[list[Device]]:
        """Add bytes and return every snapshot that is now complete."""
        self._buffer.extend(chunk)
        snapshots: list[list[Device]] = []
        while True:
            if len(self._buffer) < 4:
                return snapshots
            header = bytes(self._buffer[:4])
            try:
                length = int(header.decode("ascii"), 16)
            except (UnicodeDecodeError, ValueError) as exc:
                raise TrackFormatError(
                    f"expected a hexadecimal frame length, got {header!r}"
                ) from exc
            if length > MAX_FRAME:
                raise TrackFormatError(f"frame length {length} is implausible")
            if len(self._buffer) < 4 + length:
                return snapshots
            payload = bytes(self._buffer[4 : 4 + length])
            del self._buffer[: 4 + length]
            # Decode only once the frame is whole: a UTF-8 model name split
            # across two reads would otherwise fail to decode.
            snapshots.append(parse_devices(payload.decode("utf-8", "replace")))


def parse_mdns(text: str) -> list[tuple[str, str, str]]:
    """Parse ``adb mdns services`` into (instance, service, address) triples.

    Android advertises two services: ``_adb-tls-pairing._tcp`` while the pairing
    dialog is open, and ``_adb-tls-connect._tcp`` for an already-paired device.
    They use different ports, which is the single most common reason pairing
    fails for people doing it by hand.
    """
    found: list[tuple[str, str, str]] = []
    for line in _normalise(text).splitlines():
        line = line.strip()
        if not line or line.startswith("List of discovered") or line.startswith("*"):
            continue
        parts = line.split()
        if len(parts) >= 3 and "._tcp" in parts[1]:
            found.append((parts[0], parts[1], parts[2]))
    return found


def parse_ip(text: str) -> str | None:
    """Pull the phone's wlan address out of ``ip route`` or ``ifconfig`` output."""
    match = re.search(r"\bsrc\s+(\d{1,3}(?:\.\d{1,3}){3})", _normalise(text))
    if match:
        return match.group(1)
    match = re.search(r"inet\s+(?:addr:)?(\d{1,3}(?:\.\d{1,3}){3})", _normalise(text))
    if match and not match.group(1).startswith("127."):
        return match.group(1)
    return None


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #


def start_server() -> None:
    """Make sure the adb daemon is up.

    Worth doing explicitly before anything else, because the first call
    otherwise pays a one-second daemon start and any failure (port 5037 taken)
    shows up as a confusingly empty device list.
    """
    _text(["start-server"], timeout=20.0)


def devices() -> list[Device]:
    return parse_devices(_text(["devices", "-l"]))


def probe(serial: str) -> dict:
    """Collect model, Android version, resolution and battery in one round trip.

    ``-T`` disables the pty (and therefore CRLF translation); ``-n`` stops the
    remote shell from consuming our stdin.
    """
    return parse_probe(_text(["-s", serial, "shell", "-T", "-n", PROBE_SCRIPT]))


def enrich(device: Device) -> Device:
    """Return a copy of ``device`` with the probe results filled in."""
    if not device.ready:
        return device
    data = probe(device.serial)
    return replace(
        device,
        manufacturer=data.get("manufacturer"),
        android_release=data.get("android_release"),
        width=data.get("width"),
        height=data.get("height"),
        battery=data.get("battery"),
        # `adb devices -l` gives a model with underscores; the phone's own
        # ro.product.model is better written, so prefer it when we have it.
        model=data.get("model") or device.model,
        enriched=True,
    )


def screencap(serial: str) -> bytes:
    """Grab the screen as PNG bytes.

    ``exec-out`` is the binary-safe channel; ``adb shell screencap`` would go
    through a pty and corrupt the PNG.
    """
    data = _run(
        ["-s", serial, "exec-out", "screencap", "-p"],
        timeout=SCREENCAP_TIMEOUT,
        binary=True,
    )
    assert isinstance(data, bytes)
    if not data.startswith(b"\x89PNG"):
        raise AdbError("the device did not return a screenshot")
    return data


def connect(address: str) -> str:
    """Connect over TCP/IP. Returns adb's own message, which may be a refusal."""
    if ":" not in address:
        address = f"{address}:{DEFAULT_ADB_PORT}"
    out = _text(["connect", address], check=False, timeout=20.0).strip()
    if "connected to" not in out:
        raise AdbError(out or f"could not connect to {address}")
    return out


def disconnect(address: str) -> str:
    return _text(["disconnect", address], check=False).strip()


def pair(address: str, code: str) -> str:
    """Pair with a device using Android 11+ wireless debugging.

    The pairing port is *not* the connection port; the user reads both off the
    phone's Wireless debugging screen.
    """
    out = _text(["pair", address, code], check=False, timeout=30.0).strip()
    if "uccessfully paired" not in out:
        raise AdbError(out or "pairing failed")
    return out


def tcpip(serial: str, port: int = DEFAULT_ADB_PORT) -> None:
    """Restart the device's adbd on TCP, so USB can be unplugged."""
    _text(["-s", serial, "tcpip", str(port)], timeout=20.0)


def device_ip(serial: str) -> str | None:
    """Best guess at the phone's own wireless address."""
    try:
        out = _text(["-s", serial, "shell", "-T", "-n", "ip route get 1 2>/dev/null"])
    except AdbError:
        return None
    return parse_ip(out)


def reconnect_offline() -> str:
    """Kick offline and unauthorized devices into renegotiating.

    Note this is `reconnect offline`, never `kill-server`: killing the server
    would disconnect Android Studio or Waydroid from their own devices.
    """
    return _text(["reconnect", "offline"], check=False).strip()


def mdns_services() -> list[tuple[str, str, str]]:
    try:
        return parse_mdns(_text(["mdns", "services"], check=False, timeout=10.0))
    except AdbError:
        return []
