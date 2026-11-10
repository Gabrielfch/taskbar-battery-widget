import os

os.environ["QT_QPA_PLATFORM"] = "offscreen"

import pytest
from PySide6.QtWidgets import QApplication

from taskbar_battery_widget import widget as w
from taskbar_battery_widget.models import Device


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


class FakeCfg:
    def __init__(self, **kw):
        self.d = {"low_threshold": 20, "show_disconnected": False, "show_when_empty": False}
        self.d.update(kw)

    def get(self, k):
        return self.d[k]


def test_layout_horizontal():
    rects, (tw, th) = w.layout_pills([60, 70], 28)
    assert rects[0] == (2, 2, 60, 28)
    assert rects[1][0] == 2 + 60 + w.SPACING
    assert (tw, th) == (2 + 60 + w.SPACING + 70 + 2, 32)


def test_layout_vertical_and_empty():
    rects, (tw, th) = w.layout_pills([60, 70], 28, vertical=True)
    assert all(r[2] == 70 for r in rects) and tw == 74
    assert w.layout_pills([], 28) == ([], (0, 0))


def test_pill_height_clamp_and_hit_test():
    assert w.pill_height_for(48) == 32
    assert w.pill_height_for(30) == 20
    rects, _ = w.layout_pills([60, 70], 28)
    assert w.pill_index_at(rects, 10, 10) == 0
    assert w.pill_index_at(rects, 0, 0) == -1


def test_widget_pills(qapp):
    wd = w.TaskbarWidget(FakeCfg())
    devs = [Device("1", "KB", "keyboard", 80, True), Device("2", "M", "mouse", 10, True, charging=True),
            Device("3", "H", "headset", None, False)]
    wd._devices = devs
    wd._rebuild()
    assert wd.pill_texts() == ["80%", "10%"]
    assert wd._pills[1].low and wd._pills[1].charging
    assert wd.width() > 0 and wd.height() == wd._pill_h + 4
    wd._cfg = FakeCfg(show_disconnected=True)
    wd._rebuild()
    assert wd.pill_texts() == ["80%", "10%", "--"]
    wd._radio_on = False
    wd._rebuild()
    assert len(wd._pills) == 1
