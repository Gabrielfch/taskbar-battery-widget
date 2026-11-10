"""Data model shared by the scanner, monitor and UI."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

KINDS = (
    "keyboard", "mouse", "headset", "gamepad", "speaker", "phone", "tablet", "watch",
    "computer", "pen", "remote", "microphone", "camera", "tv", "car", "printer", "generic",
)


@dataclass
class Device:
    id: str
    name: str
    kind: str
    battery: int | None
    connected: bool
    address: str = ""
    charging: bool = False
    link: str = "bluetooth"     # "bluetooth" | "2.4ghz" | "usb"
    updated: float | None = None    # epoch seconds of the reading, when known
    idle: bool = False              # asleep 2.4 GHz device shown with its last known value
    source: str = ""                # "hid" when read directly from a dongle, else ""


_KEY_RE = re.compile(r"^h:[0-9a-f]{16}$")


def visibility_key(dev_id: str) -> str:
    """Stable, non-reversible key for a device id ("h:" + 16 hex of sha256).

    Device ids may embed Bluetooth MAC addresses; the settings file only stores these keys.
    """
    return "h:" + hashlib.sha256(str(dev_id).encode("utf-8", "replace")).hexdigest()[:16]


def is_visibility_key(value: str) -> bool:
    return isinstance(value, str) and bool(_KEY_RE.match(value))
