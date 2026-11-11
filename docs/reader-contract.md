# Vendor reader contract

Every vendor module in `taskbar_battery_widget/` that reads battery over raw HID follows this contract so
`wireless.py` can run it inside the guarded scan budget.

```python
VENDOR_IDS: tuple[int, ...]          # USB vendor ids this module handles (used for HID enumeration)

def read(infos: list[hid_win.HidInfo], deadline: float, cancel: threading.Event | None,
         handle_factory=None) -> list[models.Device]:
    ...
```

Rules
- Never raises; logs at debug level and skips a device on any error.
- Checks `cancel` and `time.monotonic() < deadline` before every write and inside read loops; per-device
  budget <= 1.5 s.
- Opens only the exact collection(s) the protocol needs (match vid, pid, usage page, usage, report lengths).
- Sends only read/query commands from a module-level whitelist; building any other request raises
  `ValueError` (unit-tested). Never set configuration, never firmware/DFU commands.
- `handle_factory(path, in_len) -> handle` lets tests inject a fake handle with
  `write(data, timeout_ms)`, `read(timeout_ms)`, `drain()`, `get_feature(report_id, length)`,
  `set_feature(data)` (only if the protocol requires a feature *query*), `get_input_report(id, length)`,
  `close()` / context manager. Default: `hid_win.HidHandle`.
- Device: `Device(id=f"<vendor>:{pid:04x}", name=naming.sanitize_name(product) or "<model name>",
  kind=..., battery=0..100 or None, charging=bool, connected=bool, link="2.4ghz"|"usb"|"bluetooth",
  source="hid")`. Headset/controller powered off but dongle present -> `connected=False, battery=None`.
- Voltage-based devices: write an original piecewise-linear curve; do not copy curve constants.
- Protocol facts may come from public references (e.g. HeadsetControl, Solaar, Linux drivers). Do not copy
  their code, comments or tables verbatim; GPL code must not be pasted.
- Tests in `tests/test_<module>.py` with fake handles only (no real HID): request bytes per model,
  reply parsing (valid, short, unrelated, error), whitelist refusal, deadline/cancel before write,
  device-off path, collection selection.
