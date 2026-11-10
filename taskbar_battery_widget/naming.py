"""Device name cleaning and kind detection (CoD, LE appearance, keywords)."""
from __future__ import annotations

import re

_SUFFIXES = [
    r"hands-?\s?free(?:\s+(?:AG|HF))?",
    r"manos\s+libres(?:\s+(?:AG|HF))?",
    r"stereo", r"est[eé]reo",
    r"audio",
    r"avrcp\s+transport", r"transporte\s+avrcp",
]
_SUFFIX_RE = re.compile(r"[\s\-_]+(?:" + "|".join(_SUFFIXES) + r")\s*$", re.IGNORECASE)
_PREFIX_RE = re.compile(r"^\s*(?:avrcp\s+transport\s+for|transporte\s+avrcp\s+para)\s+", re.IGNORECASE)


def clean_name(name: str) -> str:
    """Strip profile/transport decorations from a Windows device name."""
    original = (name or "").strip()
    s = _PREFIX_RE.sub("", original)
    while True:
        new = _SUFFIX_RE.sub("", s).strip()
        if new == s:
            break
        s = new
    s = re.sub(r"\s{2,}", " ", s).strip()
    return s or original


def kind_from_cod(major: int | None, minor: int | None) -> str | None:
    """Map Bluetooth Class of Device major/minor (minor = 6-bit field) to a kind."""
    if major is None:
        return None
    minor = minor or 0
    if major == 1:  # computer
        if minor in (4, 5):
            return "tablet"
        if minor == 7:
            return "watch"
        return "computer"
    if major == 2:  # phone
        return "phone"
    if major == 4:  # audio/video
        table = {
            1: "headset", 2: "headset", 4: "microphone", 5: "speaker", 6: "headset",
            7: "speaker", 8: "car", 10: "speaker", 12: "camera", 13: "camera",
            14: "tv", 15: "tv", 18: "gamepad",
        }
        return table.get(minor, "headset" if minor == 0 else "speaker")
    if major == 5:  # peripheral
        hi = minor & 0x30
        if hi in (0x10, 0x30):
            return "keyboard"
        if hi == 0x20:
            return "mouse"
        low = minor & 0x0F
        return {1: "gamepad", 2: "gamepad", 3: "remote", 5: "pen", 7: "pen"}.get(low)
    if major == 6:  # imaging
        if minor & 32:
            return "printer"
        if minor & 8:
            return "camera"
        return None
    if major == 7:  # wearable
        return "watch" if minor in (0, 1, 2) else None
    if major == 8:  # toy
        return "gamepad" if minor == 5 else None
    return None


def kind_from_appearance(app: int | None) -> str | None:
    """Map a BLE GAP appearance value (category = app >> 6) to a kind."""
    if app is None or app <= 0:
        return None
    cat, sub = app >> 6, app & 0x3F
    if cat == 1:
        return "phone"
    if cat == 2:
        if sub == 7:
            return "tablet"
        if sub == 6:
            return "watch"
        return "computer"
    if cat == 3:
        return "watch"
    if cat == 5:
        return "tv"
    if cat == 6:
        return "remote"
    if cat == 10:
        return "speaker"
    if cat == 15:  # HID
        return {1: "keyboard", 2: "mouse", 3: "gamepad", 4: "gamepad", 5: "pen", 7: "pen"}.get(sub)
    return None


# (kind, substring keywords, whole-word keywords); order = priority.
_KEYWORDS = [
    ("gamepad", ("controller", "gamepad", "xbox", "dualsense", "dualshock", "joy-con", "joycon",
                 "mando", "gamesir", "8bitdo"), ()),
    ("keyboard", ("keyboard", "teclado"), ("keys", "k380", "k780")),
    ("mouse", ("mouse", "ratón", "raton", "trackball", "mx master", "mx anywhere", "trackpad"), ()),
    ("headset", ("headset", "headphone", "earbud", "earphone", "airpods", "auricular", "casco",
                 "wh-1000", "wf-1000", "kraken", "barracuda", "arctis"), ("buds",)),
    ("speaker", ("speaker", "soundbar", "altavoz", "boombox"), ("boom", "flip", "echo")),
    ("microphone", ("microphone", "micrófono", "microfono"), ("mic",)),
    ("phone", ("iphone", "phone", "galaxy", "móvil", "movil", "teléfono", "telefono"), ("pixel",)),
    ("tablet", ("tablet", "ipad"), ()),
    ("watch", ("watch", "reloj", "fitbit", "garmin"), ("band",)),
    ("pen", ("stylus", "lápiz", "lapiz"), ("pen",)),
    ("remote", ("remote",), ()),
    ("camera", ("camera", "cámara", "camara", "gopro"), ()),
    ("tv", ("television", "televisión", "chromecast", "bravia"), ("tv",)),
    ("car", ("carkit", "coche"), ("car",)),
    ("printer", ("printer", "impresora"), ()),
    ("computer", ("laptop", "desktop", "notebook", "portátil", "portatil", "computer"), ("pc",)),
]


def kind_from_name(name: str) -> str:
    n = (name or "").lower()
    for kind, subs, words in _KEYWORDS:
        if any(s in n for s in subs):
            return kind
        for w in words:
            if re.search(r"(?<![a-z0-9])" + re.escape(w) + r"(?![a-z0-9])", n):
                return kind
    return "generic"


def sanitize_name(name: str, max_len: int = 48) -> str:
    """Device-supplied names are untrusted: printable chars only, no markup characters."""
    s = "".join(ch for ch in (name or "") if ch.isprintable() and ch not in "<>&")
    s = re.sub(r"\s{2,}", " ", s).strip()
    return s[:max_len].rstrip()
