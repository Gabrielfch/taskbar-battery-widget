"""DeviceMonitor: watchers + 30 s poll on a background asyncio thread, Qt signals to the UI."""
from __future__ import annotations

import asyncio
import logging
import threading
import time
import warnings

from PySide6.QtCore import QObject, Signal, Slot

from . import scanner, wireless
from .charge import ChargeEstimator
from .hid_win import GuardedCall

log = logging.getLogger(__name__)

POLL_S = 30.0
RESTART_BACKOFF_S = 5.0
START_TIMEOUT_S = 5.0
DEBOUNCE_S = 1.0
WIRELESS_TIMEOUT_S = getattr(wireless, "TOTAL_BUDGET_S", 3.6) + 0.4
BT_TIMEOUT_S = 10.0


class DeviceMonitor(QObject):
    devices_changed = Signal(list)   # list[Device]
    radio_changed = Signal(bool)
    ready = Signal()                 # worker initialised (watchers set up); emitted from the worker

    # Internal cross-thread hops: emitted from the worker thread, handled on this object's thread.
    _devices_ready = Signal(list)
    _radio_ready = Signal(bool)

    def __init__(self, parent=None, use_watchers: bool = True, cfg=None, wireless_scan=None):
        super().__init__(parent)
        self._cfg = cfg
        self._wireless_scan = wireless_scan or wireless.scan_wireless
        self._wireless_call = None          # daemon scan thread still running (maybe hung)
        self._last_wireless: list = []
        self._last_bt: list = []
        self._last_sig = None
        self._use_watchers = use_watchers
        self._devices_ready.connect(self._on_devices)
        self._radio_ready.connect(self._on_radio)
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._wake: asyncio.Event | None = None
        self._immediate = False
        self._stopping = False
        self._watchers: list = []
        self._radios: list = []
        self._estimator = ChargeEstimator()
        self._radio_on: bool | None = None
        self._ready = threading.Event()
        self._stop_evt = threading.Event()
        self._main_task: asyncio.Task | None = None

    # ---- public API -------------------------------------------------------------------
    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stopping = False
        self._stop_evt.clear()
        self._ready.clear()
        self._thread = threading.Thread(target=self._thread_main, name="DeviceMonitor", daemon=True)
        self._thread.start()            # non-blocking: the `ready` signal / wait_ready() report init

    def wait_ready(self, timeout: float = START_TIMEOUT_S) -> bool:
        """Block until the worker finished initialising (never call from the Qt thread)."""
        return self._ready.wait(timeout)

    def stop(self):
        self._stopping = True
        self._stop_evt.set()
        loop = self._loop
        if loop and not loop.is_closed():
            try:
                loop.call_soon_threadsafe(self._cancel_main)
            except RuntimeError:
                pass
        t = self._thread
        if t and t is not threading.current_thread():
            t.join(5)
            if not t.is_alive():
                self._thread = None

    def _cancel_main(self):
        self._set_wake()
        task = self._main_task
        if task is not None and not task.done():
            task.cancel()

    def refresh(self):
        """Rescan immediately (no debounce)."""
        self._poke(immediate=True)

    # ---- Qt-thread slots --------------------------------------------------------------
    @Slot(list)
    def _on_devices(self, devices):
        self.devices_changed.emit(devices)

    @Slot(bool)
    def _on_radio(self, on):
        self.radio_changed.emit(on)

    # ---- worker thread ----------------------------------------------------------------
    def _set_wake(self):
        if self._wake is not None:
            self._wake.set()

    def _poke(self, immediate=False):
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        def _do():
            if immediate:
                self._immediate = True
            self._set_wake()
        try:
            loop.call_soon_threadsafe(_do)
        except RuntimeError:
            pass

    def _thread_main(self):
        try:
            while not self._stopping:
                self._run_loop_once()
                if self._stopping:
                    break
                log.warning("DeviceMonitor loop failed; restarting in %.0f s", RESTART_BACKOFF_S)
                self._ready.set()           # never leave start() waiting on a failing init
                if self._stop_evt.wait(RESTART_BACKOFF_S):
                    break
        finally:
            self._ready.set()

    def _run_loop_once(self) -> None:
        """Run one event loop until stop is requested (returns normally) or it crashes."""
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        try:
            self._main_task = loop.create_task(self._run())
            loop.run_until_complete(self._main_task)
        except asyncio.CancelledError:
            pass
        except Exception:
            log.exception("DeviceMonitor worker crashed")
        finally:
            self._main_task = None
            try:
                pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
                for t in pending:
                    t.cancel()
                if pending:
                    loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                loop.run_until_complete(loop.shutdown_asyncgens())
            except Exception:
                log.exception("DeviceMonitor loop cleanup failed")
            try:
                loop.close()
            except Exception:
                pass
            self._loop = None
            self._wake = None

    async def _run(self):
        self._wake = asyncio.Event()
        self._immediate = True
        try:
            if self._use_watchers:
                await self._setup_watchers()
            if not self._ready.is_set():
                self._ready.set()           # init done
                self.ready.emit()
            while not self._stopping:
                await self._cycle()
                try:
                    await asyncio.wait_for(self._wake.wait(), POLL_S)
                except asyncio.TimeoutError:
                    pass
                if self._stopping:
                    break
                if not self._immediate and self._wake.is_set():
                    await asyncio.sleep(DEBOUNCE_S)   # coalesce bursts of watcher events
                self._wake.clear()
                self._immediate = False
        finally:
            self._teardown_watchers()

    def _wireless_enabled(self) -> bool:
        if self._cfg is None:
            return True
        try:
            return bool(self._cfg.get("wireless_enabled"))
        except Exception:
            return True

    def _filter_sources(self, devices) -> list:
        """Wireless devices are shown only while the 2.4 GHz source is enabled."""
        return list(devices) if self._wireless_enabled() else []

    async def _scan_wireless(self) -> list:
        """Run the wireless scan in a daemon thread, bounded by WIRELESS_TIMEOUT_S.

        On timeout or failure the last good result is returned; a hung scan can neither block the
        poll loop nor keep the process alive at exit.
        """
        hid_on = self._wireless_enabled()
        if not hid_on:
            return []
        prev = self._wireless_call
        if prev is not None and not prev.done.is_set():
            return self._filter_sources(self._last_wireless)    # still hung: do not stack threads
        try:
            wireless.set_hid_enabled(hid_on)
            wireless.set_experimental_readers(
                False if self._cfg is None else bool(self._cfg.get("experimental_readers")))
            wireless.set_dualsense_bt_extended(
                True if self._cfg is None else bool(self._cfg.get("dualsense_bt_extended")))
            if self._cfg is not None:
                wireless.set_idle_grace_min(self._cfg.get("idle_grace_min"))
            else:
                wireless.set_idle_grace_min(30)
        except Exception:
            pass
        call = GuardedCall(self._wireless_scan, "wireless-scan")
        self._wireless_call = call
        try:
            fut = asyncio.get_running_loop().create_future()

            def _poll_done():
                if fut.done():
                    return
                if call.done.is_set():
                    fut.set_result(None)
                else:
                    asyncio.get_running_loop().call_later(0.05, _poll_done)
            _poll_done()
            await asyncio.wait_for(fut, WIRELESS_TIMEOUT_S)
        except asyncio.TimeoutError:
            log.warning("wireless scan exceeded %.1f s; using cached values", WIRELESS_TIMEOUT_S)
            return self._filter_sources(self._last_wireless)
        if call.error is not None:
            log.error("wireless scan failed: %r", call.error)
            return self._filter_sources(self._last_wireless)
        self._last_wireless = list(call.value or [])
        return self._filter_sources(self._last_wireless)

    @staticmethod
    def _signature(devices, now: float | None = None) -> tuple:
        """What the UI can show: includes idle and the age in whole minutes ("N min ago")."""
        now = time.time() if now is None else now
        return tuple((d.id, d.name, d.battery, d.connected, d.charging, d.link,
                      bool(getattr(d, "idle", False)),
                      None if getattr(d, "updated", None) is None
                      else max(0, int((now - d.updated) // 60)))
                     for d in devices)

    def _publish(self, devices) -> None:
        self._last_sig = self._signature(devices)
        self._devices_ready.emit(devices)

    async def _scan_bluetooth(self) -> tuple[list, bool]:
        """Radio check + BT scan bounded by BT_TIMEOUT_S. Returns (devices, fresh).

        On timeout or error the last known list is reused (fresh=False) so the wireless scan and the
        poll loop carry on.
        """
        async def _go():
            on = await scanner.is_radio_on()
            if on != self._radio_on:
                self._radio_on = on
                self._radio_ready.emit(on)
            return await scanner.scan() if on else []

        try:
            return list(await asyncio.wait_for(_go(), BT_TIMEOUT_S)), True
        except asyncio.TimeoutError:
            log.error("device scan exceeded %.1f s; using last known Bluetooth list", BT_TIMEOUT_S)
        except Exception:
            log.exception("device scan failed")
        return list(self._last_bt), False

    async def _cycle(self):
        bt, fresh = await self._scan_bluetooth()
        if fresh:
            self._last_bt = bt
            now = time.monotonic()
            for d in bt:
                if getattr(d, "link", "bluetooth") == "bluetooth":
                    d.charging = self._estimator.update(d.id, d.battery if d.connected else None, now)
            self._estimator.retain(d.id for d in bt)
        cached = self._filter_sources(self._last_wireless)
        first = wireless.merge_devices(bt, cached)              # Bluetooth first, never wait on HID
        if self._signature(first) != self._last_sig:
            self._publish(first)
        if not self._wireless_enabled():
            return
        extra = await self._scan_wireless()
        devices = wireless.merge_devices(bt, extra)
        if self._signature(devices) != self._last_sig:
            self._publish(devices)

    # ---- watchers (best effort; polling is the fallback) --------------------------------
    async def _setup_watchers(self):
        loop = self._loop
        done: set[int] = set()

        def trigger(*_args):
            try:
                loop.call_soon_threadsafe(self._on_watch_event)
            except RuntimeError:
                pass

        try:
            from winrt.windows.devices.bluetooth import BluetoothDevice, BluetoothLEDevice
            from winrt.windows.devices.enumeration import DeviceInformation, DeviceInformationKind

            specs = []
            for cls in (BluetoothDevice, BluetoothLEDevice):
                specs.append((cls.get_device_selector_from_pairing_state(True),
                              scanner.AEP_PROPS, None))
            specs.append((scanner.NODE_AQS, scanner.NODE_PROPS, DeviceInformationKind.DEVICE))
            for aqs, props, kind in specs:
                try:
                    with warnings.catch_warnings():
                        warnings.simplefilter("ignore")
                        if kind is None:
                            w = DeviceInformation.create_watcher_aqs_filter_and_additional_properties(
                                aqs, props)
                        else:
                            w = DeviceInformation.create_watcher_with_kind_aqs_filter_and_additional_properties(
                                aqs, props, kind)
                    ready = {"v": False}

                    def on_enum_done(*_a, ready=ready):
                        ready["v"] = True

                    def on_change(*_a, ready=ready):
                        if ready["v"]:      # ignore the initial enumeration burst
                            trigger()

                    tokens = (w.add_added(on_change), w.add_updated(on_change),
                              w.add_removed(on_change), w.add_enumeration_completed(on_enum_done))
                    w.start()
                    self._watchers.append((w, tokens, on_change, on_enum_done))
                except Exception:
                    continue
        except Exception:
            pass

        try:
            from winrt.windows.devices.radios import Radio, RadioKind

            for r in await Radio.get_radios_async():
                if r.kind == RadioKind.BLUETOOTH:
                    tok = r.add_state_changed(trigger)
                    self._radios.append((r, tok, trigger))
        except Exception:
            pass

    def _on_watch_event(self):
        self._set_wake()

    def _teardown_watchers(self):
        for w, tokens, _a, _b in self._watchers:
            for remove, tok in zip(("remove_added", "remove_updated", "remove_removed",
                                    "remove_enumeration_completed"), tokens):
                try:
                    getattr(w, remove)(tok)
                except Exception:
                    pass
            try:
                w.stop()
            except Exception:
                pass
        for r, tok, _cb in self._radios:
            try:
                r.remove_state_changed(tok)
            except Exception:
                pass
        self._watchers.clear()
        self._radios.clear()
