"""Minimal ctypes Win32 HID layer: enumerate HID interfaces and talk to them.

Handles are always opened with FILE_SHARE_READ | FILE_SHARE_WRITE so vendor software
(Logitech G HUB, ...) keeps working. Only used for read/query traffic.
"""
from __future__ import annotations

import ctypes
import logging
import re
import sys
import threading
from ctypes import wintypes
from dataclasses import dataclass

log = logging.getLogger(__name__)

_WIN = sys.platform == "win32"

GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
FILE_SHARE_READ = 1
FILE_SHARE_WRITE = 2
OPEN_EXISTING = 3
FILE_FLAG_OVERLAPPED = 0x40000000
INVALID_HANDLE = ctypes.c_void_p(-1).value
ERROR_IO_PENDING = 997
WAIT_OBJECT_0 = 0
WAIT_TIMEOUT = 258
ERROR_OPERATION_ABORTED = 995
CR_SUCCESS = 0
CM_GET_DEVICE_INTERFACE_LIST_PRESENT = 0
HIDP_STATUS_SUCCESS = 0x00110000


class _OVERLAPPED(ctypes.Structure):
    _fields_ = [("Internal", ctypes.c_size_t), ("InternalHigh", ctypes.c_size_t),
                ("Offset", wintypes.DWORD), ("OffsetHigh", wintypes.DWORD),
                ("hEvent", wintypes.HANDLE)]


class _GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]


class _HIDD_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("Size", wintypes.ULONG), ("VendorID", wintypes.USHORT),
                ("ProductID", wintypes.USHORT), ("VersionNumber", wintypes.USHORT)]


class _HIDP_CAPS(ctypes.Structure):
    _fields_ = [("Usage", wintypes.USHORT), ("UsagePage", wintypes.USHORT),
                ("InputReportByteLength", wintypes.USHORT),
                ("OutputReportByteLength", wintypes.USHORT),
                ("FeatureReportByteLength", wintypes.USHORT),
                ("Reserved", wintypes.USHORT * 17),
                ("NumberLinkCollectionNodes", wintypes.USHORT),
                ("NumberInputButtonCaps", wintypes.USHORT),
                ("NumberInputValueCaps", wintypes.USHORT),
                ("NumberInputDataIndices", wintypes.USHORT),
                ("NumberOutputButtonCaps", wintypes.USHORT),
                ("NumberOutputValueCaps", wintypes.USHORT),
                ("NumberOutputDataIndices", wintypes.USHORT),
                ("NumberFeatureButtonCaps", wintypes.USHORT),
                ("NumberFeatureValueCaps", wintypes.USHORT),
                ("NumberFeatureDataIndices", wintypes.USHORT)]


_VIDPID_RE = re.compile(r"vid(?:_|&000[12])([0-9a-f]{4})[&_]pid(?:_|&)?([0-9a-f]{4})", re.I)
_COL_RE = re.compile(r"&col(\d+)", re.I)


def redact_path(path: str) -> str:
    """Log-safe form of a HID interface path: 'vid:pid[ colNN]' only. The instance part of
    Bluetooth paths carries the device MAC address and must never be logged."""
    p = path or ""
    m = _VIDPID_RE.search(p)
    if not m:
        return "<hid>"
    col = _COL_RE.search(p)
    return f"{m.group(1).lower()}:{m.group(2).lower()}" + (f" col{col.group(1)}" if col else "")


@dataclass
class HidInfo:
    path: str
    vid: int = 0
    pid: int = 0
    usage_page: int = 0
    usage: int = 0
    in_len: int = 0
    out_len: int = 0
    feat_len: int = 0
    product: str = ""


if _WIN:
    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _hid = ctypes.WinDLL("hid")
    _cfg = ctypes.WinDLL("cfgmgr32")

    _k32.CreateFileW.restype = wintypes.HANDLE
    _k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                                 wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    _k32.CloseHandle.argtypes = [wintypes.HANDLE]
    _k32.CreateEventW.restype = wintypes.HANDLE
    _k32.CreateEventW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
    _k32.WriteFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                               ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(_OVERLAPPED)]
    _k32.ReadFile.argtypes = _k32.WriteFile.argtypes
    _k32.GetOverlappedResult.argtypes = [wintypes.HANDLE, ctypes.POINTER(_OVERLAPPED),
                                         ctypes.POINTER(wintypes.DWORD), wintypes.BOOL]
    _k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    _k32.CancelIoEx.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
    _k32.ResetEvent.argtypes = [wintypes.HANDLE]

    _hid.HidD_GetHidGuid.argtypes = [ctypes.POINTER(_GUID)]
    _hid.HidD_GetAttributes.argtypes = [wintypes.HANDLE, ctypes.POINTER(_HIDD_ATTRIBUTES)]
    _hid.HidD_GetPreparsedData.argtypes = [wintypes.HANDLE, ctypes.POINTER(ctypes.c_void_p)]
    _hid.HidD_FreePreparsedData.argtypes = [ctypes.c_void_p]
    _hid.HidP_GetCaps.argtypes = [ctypes.c_void_p, ctypes.POINTER(_HIDP_CAPS)]
    _hid.HidP_GetCaps.restype = ctypes.c_long
    _hid.HidD_GetProductString.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.ULONG]
    _hid.HidD_SetFeature.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.ULONG]
    _hid.HidD_GetFeature.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.ULONG]
    _hid.HidD_GetInputReport.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.ULONG]

    _cfg.CM_Get_Device_Interface_List_SizeW.argtypes = [
        ctypes.POINTER(wintypes.ULONG), ctypes.POINTER(_GUID), wintypes.LPCWSTR, wintypes.ULONG]
    _cfg.CM_Get_Device_Interface_ListW.argtypes = [
        ctypes.POINTER(_GUID), wintypes.LPCWSTR, ctypes.c_void_p, wintypes.ULONG, wintypes.ULONG]


def _open_raw(path: str, access: int, overlapped: bool):
    flags = FILE_FLAG_OVERLAPPED if overlapped else 0
    h = _k32.CreateFileW(path, access, FILE_SHARE_READ | FILE_SHARE_WRITE, None, OPEN_EXISTING, flags, None)
    if h is None or h == INVALID_HANDLE:
        return None
    return h


def _interface_paths() -> list[str]:
    guid = _GUID()
    _hid.HidD_GetHidGuid(ctypes.byref(guid))
    size = wintypes.ULONG(0)
    if _cfg.CM_Get_Device_Interface_List_SizeW(ctypes.byref(size), ctypes.byref(guid), None,
                                               CM_GET_DEVICE_INTERFACE_LIST_PRESENT) != CR_SUCCESS:
        return []
    if size.value <= 1:
        return []
    buf = ctypes.create_unicode_buffer(size.value)
    if _cfg.CM_Get_Device_Interface_ListW(ctypes.byref(guid), None, buf, size.value,
                                          CM_GET_DEVICE_INTERFACE_LIST_PRESENT) != CR_SUCCESS:
        return []
    return [p for p in ctypes.wstring_at(buf, size.value).split("\0") if p]


def _path_matches_vendor(path: str, vendor_ids) -> bool:
    """Cheap pre-filter on the interface path. USB paths carry ``vid_XXXX``; Bluetooth HID paths
    carry ``vid&000SXXXX`` (S = vendor-id source). The real vendor is confirmed by HidD_GetAttributes."""
    p = path.lower()
    for v in vendor_ids:
        if f"vid_{v:04x}" in p or f"vid&0001{v:04x}" in p or f"vid&0002{v:04x}" in p:
            return True
    return False


def enumerate_hid(vendor_ids: tuple[int, ...] | None = None) -> list[HidInfo]:
    """List HID interfaces (optionally only given vendor ids). Never raises."""
    if not _WIN:
        return []
    out: list[HidInfo] = []
    try:
        paths = _interface_paths()
    except Exception:
        log.exception("HID interface enumeration failed")
        return []
    for path in paths:
        if vendor_ids is not None and not _path_matches_vendor(path, vendor_ids):
            continue
        h = _open_raw(path, 0, False)           # query-only access: works even for keyboards/mice
        if h is None:
            continue
        try:
            attr = _HIDD_ATTRIBUTES()
            attr.Size = ctypes.sizeof(attr)
            if not _hid.HidD_GetAttributes(h, ctypes.byref(attr)):
                continue
            info = HidInfo(path=path, vid=attr.VendorID, pid=attr.ProductID)
            pp = ctypes.c_void_p()
            if _hid.HidD_GetPreparsedData(h, ctypes.byref(pp)):
                try:
                    caps = _HIDP_CAPS()
                    if _hid.HidP_GetCaps(pp, ctypes.byref(caps)) == HIDP_STATUS_SUCCESS:
                        info.usage_page, info.usage = caps.UsagePage, caps.Usage
                        info.in_len = caps.InputReportByteLength
                        info.out_len = caps.OutputReportByteLength
                        info.feat_len = caps.FeatureReportByteLength
                finally:
                    _hid.HidD_FreePreparsedData(pp)
            sbuf = ctypes.create_unicode_buffer(128)
            if _hid.HidD_GetProductString(h, sbuf, ctypes.sizeof(sbuf)):
                info.product = sbuf.value
            out.append(info)
        except Exception:
            log.debug("HID probe failed for %s", redact_path(path), exc_info=True)
        finally:
            _k32.CloseHandle(h)
    return out


class GuardedCall:
    """Run `fn` in a daemon thread so a hung driver call can never block the caller or app exit.

    `wait(timeout)` returns True when the call finished; a result that arrives later is ignored by
    callers that already gave up (they also set their cancel flag).
    """

    def __init__(self, fn, name: str = "hid-guarded"):
        self.value = None
        self.error: BaseException | None = None
        self.done = threading.Event()
        self._fn = fn
        self._thread = threading.Thread(target=self._run, name=name, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        try:
            self.value = self._fn()
        except BaseException as exc:        # noqa: BLE001 - reported through .error
            self.error = exc
        finally:
            self.done.set()

    def wait(self, timeout: float) -> bool:
        return self.done.wait(max(0.0, timeout))


class HidHandle:
    """Shared read/write handle. write/read are overlapped; get/set_feature are synchronous.

    read() returns None on timeout and raises OSError on a permanent failure. close() cancels any
    pending overlapped read and waits for the cancellation before the event/file handles are freed.
    """

    def __init__(self, path: str, in_len: int = 64):
        self.path = path
        self._in_len = in_len
        self._lock = threading.RLock()          # serialises handle operations against close()
        self._closing = False
        self._pending = False
        self._ovl = _OVERLAPPED()
        self._rbuf = None
        self._h = _open_raw(path, GENERIC_READ | GENERIC_WRITE, True)
        if self._h is None:
            raise OSError(f"cannot open HID path {redact_path(path)}")
        self._ev = _k32.CreateEventW(None, True, False, None)       # read completion
        self._wev = _k32.CreateEventW(None, True, False, None)      # write completion
        if not self._ev or not self._wev:
            for ev in (self._ev, self._wev):
                if ev:
                    _k32.CloseHandle(ev)
            _k32.CloseHandle(self._h)
            self._h = None
            raise OSError("CreateEventW failed")

    def close(self) -> None:
        self._closing = True
        h = self._h
        if h is None:
            return
        try:
            _k32.CancelIoEx(h, None)            # wakes a read blocked in another thread
        except Exception:
            pass
        if not self._lock.acquire(timeout=2.0):
            log.warning("HID handle busy in a hung call; leaking %s", redact_path(self.path))
            return
        try:
            if self._h is None:
                return
            if self._pending:
                n = wintypes.DWORD(0)
                _k32.GetOverlappedResult(self._h, ctypes.byref(self._ovl), ctypes.byref(n), True)
                self._pending = False
            _k32.CloseHandle(self._ev)
            _k32.CloseHandle(self._wev)
            _k32.CloseHandle(self._h)
            self._h = None
        finally:
            self._lock.release()

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()

    def _live(self):
        if self._h is None or self._closing:
            raise OSError("HID handle closed")
        return self._h

    def write(self, data: bytes, timeout_ms: int = 1000) -> bool:
        """Send an output report (first byte = report id). True only if every byte was written."""
        with self._lock:
            h = self._live()
            buf = ctypes.create_string_buffer(bytes(data), len(data))
            ovl = _OVERLAPPED()
            ovl.hEvent = self._wev
            _k32.ResetEvent(self._wev)
            n = wintypes.DWORD(0)
            ok = _k32.WriteFile(h, buf, len(data), ctypes.byref(n), ctypes.byref(ovl))
            if not ok:
                if ctypes.get_last_error() != ERROR_IO_PENDING:
                    return False
                if _k32.WaitForSingleObject(self._wev, max(0, int(timeout_ms))) != WAIT_OBJECT_0:
                    _k32.CancelIoEx(h, ctypes.byref(ovl))
                    _k32.GetOverlappedResult(h, ctypes.byref(ovl), ctypes.byref(n), True)
                    return False
                if not _k32.GetOverlappedResult(h, ctypes.byref(ovl), ctypes.byref(n), False):
                    return False
            return n.value == len(data)

    def read(self, timeout_ms: int = 1000) -> bytes | None:
        """Read one input report; None on timeout (a pending read survives across calls).

        Raises OSError when the device is gone or the handle was closed/cancelled.
        """
        with self._lock:
            h = self._live()
            if not self._pending:
                self._rbuf = ctypes.create_string_buffer(self._in_len)
                self._ovl = _OVERLAPPED()
                self._ovl.hEvent = self._ev
                _k32.ResetEvent(self._ev)
                n = wintypes.DWORD(0)
                ok = _k32.ReadFile(h, self._rbuf, self._in_len, ctypes.byref(n), ctypes.byref(self._ovl))
                if not ok and ctypes.get_last_error() != ERROR_IO_PENDING:
                    raise OSError(f"HID read failed ({ctypes.get_last_error()})")
                self._pending = True
            res = _k32.WaitForSingleObject(self._ev, max(0, int(timeout_ms)))
            if res == WAIT_TIMEOUT:
                return None
            if res != WAIT_OBJECT_0:
                raise OSError("HID wait failed")
            n = wintypes.DWORD(0)
            self._pending = False
            if not _k32.GetOverlappedResult(h, ctypes.byref(self._ovl), ctypes.byref(n), False):
                raise OSError(f"HID read aborted ({ctypes.get_last_error()})")
            return bytes(self._rbuf.raw[:n.value])

    def drain(self) -> None:
        """Discard queued input reports."""
        try:
            for _ in range(64):
                if self.read(0) is None:
                    break
        except OSError:
            pass

    def set_feature(self, data: bytes) -> bool:
        with self._lock:
            h = self._live()
            buf = ctypes.create_string_buffer(bytes(data), len(data))
            return bool(_hid.HidD_SetFeature(h, buf, len(data)))

    def get_feature(self, report_id: int, length: int) -> bytes | None:
        with self._lock:
            h = self._live()
            buf = ctypes.create_string_buffer(length)
            buf[0] = report_id
            if not _hid.HidD_GetFeature(h, buf, length):
                return None
            return bytes(buf.raw)

    def get_input_report(self, report_id: int, length: int) -> bytes | None:
        """Fetch the current input report over the control pipe (HidD_GetInputReport)."""
        with self._lock:
            h = self._live()
            buf = ctypes.create_string_buffer(length)
            buf[0] = report_id
            if not _hid.HidD_GetInputReport(h, buf, length):
                return None
            return bytes(buf.raw)
