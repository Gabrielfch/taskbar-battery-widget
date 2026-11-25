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
- The ONE explicit, opt-in exception (`razer_dongle_wake`, default off, `razer_odessa.py`): the Razer
  BlackShark V2 Pro 2.4 GHz dongle (`1532:0555`) only answers GETs after Razer's own `set_remote_mode`
  frame (`0x02/0xE1 = 1`) has been sent in the current USB session (normally by Synapse). When the user
  enables *Settings > Sources > Activate Razer dongles without Synapse* and the dongle gives no reply to
  the connection GET, the reader sends that fixed frame (twice, 35 ms apart, as Synapse does), waits
  briefly (inside the device budget) and retries the GETs. According to the contributor's hardware tests
  (PR #3) the state is volatile: it is lost when the dongle is unplugged or power-cycled. It is sent at most
  once per device path per app run; a scan that no longer sees the path allows it again (a very quick
  re-plug between two scans may therefore not resend it). If no frame could be written, a later scan may
  try again. Never after a dropped battery GET, and never when the option is off.
  The frame is kept out of the GET whitelist (`ALLOWED_PA_FRAMES`) and only `_send_wake` can write it,
  which raises `ValueError` unless the flag is on. Any other SET still raises `ValueError`.
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

## Beta reader: Areson mice (`areson.py`, `25A7:FA07` / `25A7:FA08`)
- Experimental and off by default: registered by `wireless.py` only when beta readers are enabled; without
  the flag it does no I/O. Issue #9 (Redragon M913 / M915RGB-WL, Rytaki Gamspeed V8 Pro).
- Transport: `HidD_SetFeature` on the `0xFF02` collection (feature length >= 17, opened feature-only) and
  input reports from the `0xFF01` collection of the same USB interface (`mi_XX`); other interfaces are never
  paired.
- The ONLY frame that can ever be sent is GetPower, `08 04 00 ... 00 49` (17 bytes, checksum makes the sum
  `0x55`), zero-padded to the collection's feature length. Any other feature write, any output report and any
  feature read raises `ValueError`. The link command (`03`) is never sent for FA07/FA08.
- Reply: command `04`, status 0, valid checksum, data length >= 1; level = `b[6] * 10`, charging =
  `b[5] >= 2 and b[7] != 0`; level 0 without charging yields no reading. The first reply (or its absence)
  per receiver is logged once per run at INFO as hex for diagnosis.

## Beta family: SteelSeries Rival 3 Wireless (`steelseries.py`, family `rival3`, `1038:1830`)
- Experimental and off by default (`beta=True`): `steelseries.read` skips it unless beta readers are enabled.
  Protocol facts come from public reverse-engineering notes (rivalcfg, GPL: facts only, no code copied).
- Collection: usage page `0xFFC0`, usage `0x01` (USB interface 3), input and output length >= 1.
- The ONLY frame that can be sent is `00 AA 01` (report id 0 + battery query), zero padded to the output
  report length; `build_request` / `_send` raise `ValueError` for anything else.
- Reply (report id 0 dropped): `[0]` level 0..100. Level > 100 and level 0 yield no reading (cached value
  kept). The mouse uses replaceable batteries and cannot charge, so `[2]` (a charging flag in the reference
  notes) is ignored and `charging` is always false. Observed on one unit: `04` with a nearly empty battery,
  `25` after a battery swap.
- The first reply (or its absence) per run is logged once at INFO as hex for diagnosis.
