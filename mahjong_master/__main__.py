from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import random
import sys
import time
from collections import Counter, deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import mss
from PyQt6.QtCore import QPointF, QAbstractNativeEventFilter, QRectF, Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QIcon, QImage, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGraphicsItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsTextItem,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QSpinBox,
    QStatusBar,
    QToolButton,
    QVBoxLayout,
    QWidgetAction,
    QWidget,
)

from mahjong_master.annotator import AnnotatorWindow
from mahjong_master.game_analyzer import GameAnalyzer, detections_from_yolo_result
from mahjong_master.mahjong_logic import CallDecision, HandState, Tile, tile_from_name
from mahjong_master.screen_regions import load_config, reset_config, save_config
from mahjong_master.theme import apply_dark_theme


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RAW_DATASET_DIR = PROJECT_ROOT / "dataset" / "raw"
RUNS_DIR = PROJECT_ROOT / "runs" / "detect"
CONFIG_PATH = PROJECT_ROOT / "configs.json"
ASSETS_DIR = PROJECT_ROOT / "assets"
DEFAULT_WINDOW_TITLE = "MahjongSoul-Steam"
HOTKEY_ID_SAVE_SCREENSHOT = 1
HOTKEY_ID_AUTO_PLAY = 2
VK_F3 = 0x72
VK_TAB = 0x09
WM_HOTKEY = 0x0312
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
CAPTURE_WIDTH = 1592
CAPTURE_HEIGHT = 933
PREDICT_IMAGE_SIZE = 1600
WIND_ORDER = ("east", "south", "west", "north")
WIND_LETTERS = {"E": "east", "S": "south", "W": "west", "N": "north"}
VISUAL_TURN_ORDER = ("esquerda", "principal", "direita", "cima")

user32 = ctypes.windll.user32
dwmapi = ctypes.windll.dwmapi


class Point(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class Msg(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("message", wintypes.UINT),
        ("wParam", wintypes.WPARAM),
        ("lParam", wintypes.LPARAM),
        ("time", wintypes.DWORD),
        ("pt", Point),
    ]


class Rect(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


class GlobalHotkeyFilter(QAbstractNativeEventFilter):
    def __init__(self, hotkey_id: int, virtual_key: int, callback) -> None:
        super().__init__()
        self.hotkey_id = hotkey_id
        self.virtual_key = virtual_key
        self.callback = callback
        self.registered = bool(user32.RegisterHotKey(None, hotkey_id, 0, virtual_key))

    def nativeEventFilter(self, event_type, message):  # noqa: N802
        if event_type not in (b"windows_generic_MSG", b"windows_dispatcher_MSG"):
            return False, 0

        msg = Msg.from_address(int(message))
        if msg.message == WM_HOTKEY and msg.wParam == self.hotkey_id:
            self.callback()
            return True, 0

        return False, 0

    def unregister(self) -> None:
        if self.registered:
            user32.UnregisterHotKey(None, self.hotkey_id)
            self.registered = False


def _enable_dpi_awareness() -> None:
    # Qt 6 already requests per-monitor DPI awareness on Windows.
    # Calling the Win32 API first makes Qt log an "Access is denied" warning.
    return


@dataclass(frozen=True)
class WindowInfo:
    hwnd: int
    title: str

    @property
    def label(self) -> str:
        return f"{self.title}  [0x{self.hwnd:08X}]"


def _window_title(hwnd: int) -> str:
    length = user32.GetWindowTextLengthW(hwnd)
    if length <= 0:
        return ""

    buffer = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buffer, length + 1)
    return buffer.value.strip()


def list_visible_windows() -> list[WindowInfo]:
    windows: list[WindowInfo] = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def enum_proc(hwnd: int, _lparam: int) -> bool:
        if not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
            return True

        title = _window_title(hwnd)
        if title:
            windows.append(WindowInfo(hwnd=int(hwnd), title=title))

        return True

    user32.EnumWindows(enum_proc, 0)
    return sorted(windows, key=lambda item: item.title.lower())


def window_bounds(hwnd: int) -> tuple[int, int, int, int] | None:
    rect = Rect()

    # DWMWA_EXTENDED_FRAME_BOUNDS excludes most invisible resize borders.
    result = dwmapi.DwmGetWindowAttribute(
        hwnd,
        9,
        ctypes.byref(rect),
        ctypes.sizeof(rect),
    )
    if result != 0:
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return None

    width = rect.right - rect.left
    height = rect.bottom - rect.top
    if width <= 0 or height <= 0:
        return None

    return rect.left, rect.top, width, height


def clamp_to_monitor(bounds: tuple[int, int, int, int], monitors) -> dict[str, int] | None:
    left, top, width, height = bounds
    right = left + width
    bottom = top + height

    best_area = 0
    best_rect: dict[str, int] | None = None
    for monitor in monitors[1:] or monitors:
        mon_left = int(monitor["left"])
        mon_top = int(monitor["top"])
        mon_right = mon_left + int(monitor["width"])
        mon_bottom = mon_top + int(monitor["height"])

        clipped_left = max(left, mon_left)
        clipped_top = max(top, mon_top)
        clipped_right = min(right, mon_right)
        clipped_bottom = min(bottom, mon_bottom)
        clipped_width = clipped_right - clipped_left
        clipped_height = clipped_bottom - clipped_top
        area = clipped_width * clipped_height

        if clipped_width > 0 and clipped_height > 0 and area > best_area:
            best_area = area
            best_rect = {
                "left": int(clipped_left),
                "top": int(clipped_top),
                "width": int(clipped_width),
                "height": int(clipped_height),
            }

    return best_rect


class RegionRectItem(QGraphicsRectItem):
    resize_handle_size = 18

    def __init__(self, key: str, region: dict, changed_callback) -> None:
        super().__init__(0, 0, int(region["w"]), int(region["h"]))
        self.key = key
        self.changed_callback = changed_callback
        self._resizing = False
        self._resize_start_scene = QPointF()
        self._resize_start_rect = QRectF()

        color = QColor(str(region.get("color", "#E5E7EB")))
        fill = QColor(color)
        fill.setAlpha(26)
        self.setBrush(fill)
        self.setPen(QPen(color, 2, Qt.PenStyle.DashLine))
        self.setPos(int(region["x"]), int(region["y"]))
        self.setAcceptHoverEvents(True)
        self.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsMovable
            | QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
            | QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges
        )

        self.label_item = QGraphicsTextItem(str(region.get("label", key)), self)
        self.label_item.setDefaultTextColor(color.lighter(145))
        self.label_item.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        self.label_item.setPos(4, 2)
        self.label_item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)

    def itemChange(self, change, value):  # noqa: N802
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionChange and self.scene() is not None:
            point = value
            rect = self.rect()
            x = max(0.0, min(float(point.x()), CAPTURE_WIDTH - rect.width()))
            y = max(0.0, min(float(point.y()), CAPTURE_HEIGHT - rect.height()))
            return QPointF(x, y)
        return super().itemChange(change, value)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self._is_resize_hit(event.pos()):
            self._resizing = True
            self._resize_start_scene = event.scenePos()
            self._resize_start_rect = QRectF(self.rect())
            self.setSelected(True)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._resizing:
            delta = event.scenePos() - self._resize_start_scene
            max_width = CAPTURE_WIDTH - self.pos().x()
            max_height = CAPTURE_HEIGHT - self.pos().y()
            new_width = max(8.0, min(max_width, self._resize_start_rect.width() + delta.x()))
            new_height = max(8.0, min(max_height, self._resize_start_rect.height() + delta.y()))
            self.setRect(0, 0, new_width, new_height)
            self._notify_changed(committed=False)
            event.accept()
            return
        super().mouseMoveEvent(event)
        self._notify_changed(committed=False)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if self._resizing:
            self._resizing = False
            self._notify_changed(committed=True)
            event.accept()
            return
        super().mouseReleaseEvent(event)
        self._notify_changed(committed=True)

    def hoverMoveEvent(self, event) -> None:  # noqa: N802
        if self._is_resize_hit(event.pos()):
            self.setCursor(Qt.CursorShape.SizeFDiagCursor)
        else:
            self.setCursor(Qt.CursorShape.SizeAllCursor)
        super().hoverMoveEvent(event)

    def _is_resize_hit(self, pos: QPointF) -> bool:
        rect = self.rect()
        return (
            rect.width() - self.resize_handle_size <= pos.x() <= rect.width() + 3
            and rect.height() - self.resize_handle_size <= pos.y() <= rect.height() + 3
        )

    def _notify_changed(self, committed: bool) -> None:
        rect = self.rect()
        pos = self.pos()
        self.changed_callback(
            self.key,
            round(pos.x()),
            round(pos.y()),
            round(rect.width()),
            round(rect.height()),
            committed,
        )


class PixelProbeItem(QGraphicsRectItem):
    size = 14

    def __init__(self, key: str, probe: dict, changed_callback) -> None:
        half = self.size / 2
        super().__init__(-half, -half, self.size, self.size)
        self.key = key
        self.changed_callback = changed_callback

        color = QColor(str(probe.get("color", "#FBBF24")))
        fill = QColor(color)
        fill.setAlpha(80)
        self.setBrush(fill)
        self.setPen(QPen(color, 2))
        self.setPos(int(probe["x"]), int(probe["y"]))
        self.setAcceptHoverEvents(True)
        self.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsMovable
            | QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
            | QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges
        )

        self.label_item = QGraphicsTextItem(str(probe.get("label", key)), self)
        self.label_item.setDefaultTextColor(color.lighter(150))
        self.label_item.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        self.label_item.setPos(8, -24)
        self.label_item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)

    def itemChange(self, change, value):  # noqa: N802
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionChange and self.scene() is not None:
            point = value
            x = max(0.0, min(float(point.x()), CAPTURE_WIDTH - 1))
            y = max(0.0, min(float(point.y()), CAPTURE_HEIGHT - 1))
            return QPointF(x, y)
        return super().itemChange(change, value)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        super().mouseReleaseEvent(event)
        self._notify_changed(committed=True)

    def hoverMoveEvent(self, event) -> None:  # noqa: N802
        self.setCursor(Qt.CursorShape.CrossCursor)
        super().hoverMoveEvent(event)

    def _notify_changed(self, committed: bool) -> None:
        pos = self.pos()
        self.changed_callback(self.key, round(pos.x()), round(pos.y()), committed)


class RegionEditorView(QGraphicsView):
    def __init__(self, config: dict, changed_callback, probe_changed_callback) -> None:
        super().__init__()
        self.config = config
        self.changed_callback = changed_callback
        self.probe_changed_callback = probe_changed_callback
        self.scene = QGraphicsScene(self)
        self.setScene(self.scene)
        self.setMinimumHeight(360)
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.region_items: dict[str, RegionRectItem] = {}
        self.probe_items: dict[str, PixelProbeItem] = {}
        self.selected_probe_key: str | None = None
        self._source_image: QImage | None = None

    def set_image(self, image: QImage | None) -> None:
        self._source_image = image.copy() if image is not None else None
        self.reload_regions()

    def reload_regions(self) -> None:
        self.scene.clear()
        if self._source_image is not None:
            pixmap = QPixmap.fromImage(self._source_image)
        else:
            pixmap = QPixmap(CAPTURE_WIDTH, CAPTURE_HEIGHT)
            pixmap.fill(QColor("#0B1220"))
        self.scene.addPixmap(pixmap)
        self.scene.setSceneRect(0, 0, CAPTURE_WIDTH, CAPTURE_HEIGHT)
        self.region_items.clear()
        for key, region in self.config["regions"].items():
            item = RegionRectItem(key, region, self.changed_callback)
            item.setVisible(bool(region.get("enabled", True)))
            self.scene.addItem(item)
            self.region_items[key] = item
        self.probe_items.clear()
        for key, probe in self.config.get("pixel_probes", {}).items():
            item = PixelProbeItem(key, probe, self.probe_changed_from_item)
            item.setVisible(bool(probe.get("enabled", True)))
            self.scene.addItem(item)
            self.probe_items[key] = item
        self.fitInView(self.scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    def select_key(self, key: str) -> None:
        item = self.region_items.get(key)
        if item is None:
            return
        self.scene.clearSelection()
        item.setSelected(True)
        self.selected_probe_key = None
        self.ensureVisible(item, 40, 40)

    def select_probe_key(self, key: str) -> None:
        item = self.probe_items.get(key)
        if item is None:
            return
        self.scene.clearSelection()
        self.selected_probe_key = key
        item.setSelected(True)
        self.ensureVisible(item, 40, 40)

    def update_item_from_region(self, key: str) -> None:
        item = self.region_items.get(key)
        region = self.config["regions"].get(key)
        if item is None or region is None:
            return
        item.setPos(int(region["x"]), int(region["y"]))
        item.setRect(0, 0, int(region["w"]), int(region["h"]))
        item.setVisible(bool(region.get("enabled", True)))

    def update_probe_from_config(self, key: str) -> None:
        item = self.probe_items.get(key)
        probe = self.config.get("pixel_probes", {}).get(key)
        if item is None or probe is None:
            return
        item.setPos(int(probe["x"]), int(probe["y"]))
        item.setVisible(bool(probe.get("enabled", True)))

    def probe_changed_from_item(self, key: str, x: int, y: int, committed: bool) -> None:
        color = self.sample_color_hex(x, y)
        self.probe_changed_callback(key, x, y, color, committed)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if self.selected_probe_key and event.button() == Qt.MouseButton.LeftButton:
            scene_pos = self.mapToScene(event.position().toPoint())
            x = max(0, min(CAPTURE_WIDTH - 1, round(scene_pos.x())))
            y = max(0, min(CAPTURE_HEIGHT - 1, round(scene_pos.y())))
            color = self.sample_color_hex(x, y)
            self.probe_changed_callback(self.selected_probe_key, x, y, color, True)
            self.update_probe_from_config(self.selected_probe_key)
            event.accept()
            return
        super().mousePressEvent(event)

    def sample_color_hex(self, x: int, y: int) -> str:
        if self._source_image is None or self._source_image.isNull():
            return "#FBBF24"
        color = self._source_image.pixelColor(x, y)
        return color.name(QColor.NameFormat.HexRgb)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if self.scene.sceneRect().isValid():
            self.fitInView(self.scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)


class RegionConfigDialog(QDialog):
    def __init__(self, config: dict, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Configurar areas de deteccao")
        self.resize(980, 720)
        self.config = config
        self.current_key: str | None = None
        self.current_probe_key: str | None = None

        self.editor_view = RegionEditorView(
            self.config,
            self.region_changed_from_editor,
            self.probe_changed_from_editor,
        )
        if parent is not None and getattr(parent, "last_capture", None) is not None:
            self.editor_view.set_image(parent.last_capture)
        else:
            self.editor_view.set_image(None)

        self.region_combo = QComboBox()
        for key, region in self.config["regions"].items():
            self.region_combo.addItem(f"{region['label']} ({key})", key)
        self.region_combo.currentIndexChanged.connect(self.load_selected_region)

        self.enabled_checkbox = QCheckBox("Ativa")
        self.x_input = self.region_spinbox(CAPTURE_WIDTH)
        self.y_input = self.region_spinbox(CAPTURE_HEIGHT)
        self.w_input = self.region_spinbox(CAPTURE_WIDTH)
        self.h_input = self.region_spinbox(CAPTURE_HEIGHT)

        form = QFormLayout()
        form.addRow("Area", self.region_combo)
        form.addRow("", self.enabled_checkbox)
        form.addRow("X", self.x_input)
        form.addRow("Y", self.y_input)
        form.addRow("Largura", self.w_input)
        form.addRow("Altura", self.h_input)

        self.probe_combo = QComboBox()
        for key, probe in self.config.get("pixel_probes", {}).items():
            self.probe_combo.addItem(f"{probe['label']} ({key})", key)
        self.probe_combo.currentIndexChanged.connect(self.load_selected_probe)
        self.probe_enabled_checkbox = QCheckBox("Ativo")
        self.probe_x_input = self.region_spinbox(CAPTURE_WIDTH)
        self.probe_y_input = self.region_spinbox(CAPTURE_HEIGHT)
        self.probe_tolerance_input = self.region_spinbox(255)
        self.probe_color_label = QLabel("-")
        self.probe_color_label.setMinimumWidth(90)

        probe_form = QFormLayout()
        probe_form.addRow("Pixel", self.probe_combo)
        probe_form.addRow("", self.probe_enabled_checkbox)
        probe_form.addRow("X", self.probe_x_input)
        probe_form.addRow("Y", self.probe_y_input)
        probe_form.addRow("Cor", self.probe_color_label)
        probe_form.addRow("Tolerancia", self.probe_tolerance_input)

        save_button = QPushButton("Salvar area")
        save_button.clicked.connect(self.save_current_region)
        save_probe_button = QPushButton("Salvar pixel")
        save_probe_button.clicked.connect(self.save_current_probe)
        reset_button = QPushButton("Restaurar padrao")
        reset_button.clicked.connect(self.reset_to_defaults)

        button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        button_box.rejected.connect(self.reject)

        layout = QVBoxLayout()
        layout.addWidget(self.editor_view, stretch=1)
        layout.addLayout(form)
        layout.addLayout(probe_form)
        actions = QHBoxLayout()
        actions.addWidget(save_button)
        actions.addWidget(save_probe_button)
        actions.addWidget(reset_button)
        actions.addStretch(1)
        layout.addLayout(actions)
        layout.addWidget(button_box)
        self.setLayout(layout)
        self.load_selected_region()
        self.load_selected_probe()

    def set_preview_image(self, image: QImage | None) -> None:
        self.editor_view.set_image(image)
        if self.current_key:
            self.editor_view.select_key(self.current_key)
        if self.current_probe_key:
            self.editor_view.select_probe_key(self.current_probe_key)

    def region_spinbox(self, maximum: int) -> QSpinBox:
        spinbox = QSpinBox()
        spinbox.setRange(0, maximum)
        spinbox.setSingleStep(5)
        return spinbox

    def load_selected_region(self) -> None:
        self.current_key = self.region_combo.currentData()
        if not self.current_key:
            return
        region = self.config["regions"][self.current_key]
        self.enabled_checkbox.setChecked(bool(region.get("enabled", True)))
        self.x_input.setValue(int(region["x"]))
        self.y_input.setValue(int(region["y"]))
        self.w_input.setValue(int(region["w"]))
        self.h_input.setValue(int(region["h"]))
        self.editor_view.select_key(self.current_key)

    def load_selected_probe(self) -> None:
        self.current_probe_key = self.probe_combo.currentData()
        if not self.current_probe_key:
            return
        probe = self.config["pixel_probes"][self.current_probe_key]
        self.probe_enabled_checkbox.setChecked(bool(probe.get("enabled", True)))
        self.probe_x_input.setValue(int(probe["x"]))
        self.probe_y_input.setValue(int(probe["y"]))
        self.probe_tolerance_input.setValue(int(probe.get("tolerance", 45)))
        self.set_probe_color_label(str(probe.get("color", "#FBBF24")))
        self.editor_view.select_probe_key(self.current_probe_key)

    def save_current_region(self) -> None:
        if not self.current_key:
            return
        region = self.config["regions"][self.current_key]
        region["enabled"] = self.enabled_checkbox.isChecked()
        region["x"] = self.x_input.value()
        region["y"] = self.y_input.value()
        region["w"] = max(1, min(self.w_input.value(), CAPTURE_WIDTH - region["x"]))
        region["h"] = max(1, min(self.h_input.value(), CAPTURE_HEIGHT - region["y"]))
        self.editor_view.update_item_from_region(self.current_key)
        save_config(CONFIG_PATH, self.config)
        if self.parent() and hasattr(self.parent(), "capture_once"):
            self.parent().capture_once(force=True)

    def save_current_probe(self) -> None:
        if not self.current_probe_key:
            return
        probe = self.config["pixel_probes"][self.current_probe_key]
        probe["enabled"] = self.probe_enabled_checkbox.isChecked()
        probe["x"] = self.probe_x_input.value()
        probe["y"] = self.probe_y_input.value()
        probe["tolerance"] = self.probe_tolerance_input.value()
        self.editor_view.update_probe_from_config(self.current_probe_key)
        save_config(CONFIG_PATH, self.config)
        if self.parent() and hasattr(self.parent(), "capture_once"):
            self.parent().capture_once(force=True)

    def region_changed_from_editor(
        self,
        key: str,
        x: int,
        y: int,
        width: int,
        height: int,
        committed: bool,
    ) -> None:
        region = self.config["regions"][key]
        region["x"] = x
        region["y"] = y
        region["w"] = width
        region["h"] = height
        if key == self.current_key:
            for widget, value in (
                (self.x_input, x),
                (self.y_input, y),
                (self.w_input, width),
                (self.h_input, height),
            ):
                widget.blockSignals(True)
                widget.setValue(value)
                widget.blockSignals(False)
        if committed:
            save_config(CONFIG_PATH, self.config)
            if self.parent() and hasattr(self.parent(), "capture_once"):
                self.parent().capture_once(force=True)

    def probe_changed_from_editor(
        self,
        key: str,
        x: int,
        y: int,
        color: str,
        committed: bool,
    ) -> None:
        probe = self.config["pixel_probes"][key]
        probe["x"] = x
        probe["y"] = y
        probe["color"] = color
        if key == self.current_probe_key:
            for widget, value in (
                (self.probe_x_input, x),
                (self.probe_y_input, y),
            ):
                widget.blockSignals(True)
                widget.setValue(value)
                widget.blockSignals(False)
            self.set_probe_color_label(color)
        if committed:
            save_config(CONFIG_PATH, self.config)
            if self.parent() and hasattr(self.parent(), "capture_once"):
                self.parent().capture_once(force=True)

    def set_probe_color_label(self, color: str) -> None:
        self.probe_color_label.setText(color)
        self.probe_color_label.setStyleSheet(f"QLabel {{ background: {color}; color: #FFFFFF; padding: 3px; }}")

    def reset_to_defaults(self) -> None:
        self.config.clear()
        self.config.update(reset_config(CONFIG_PATH))
        self.region_combo.blockSignals(True)
        self.region_combo.clear()
        for key, region in self.config["regions"].items():
            self.region_combo.addItem(f"{region['label']} ({key})", key)
        self.region_combo.blockSignals(False)
        self.probe_combo.blockSignals(True)
        self.probe_combo.clear()
        for key, probe in self.config.get("pixel_probes", {}).items():
            self.probe_combo.addItem(f"{probe['label']} ({key})", key)
        self.probe_combo.blockSignals(False)
        self.editor_view.reload_regions()
        self.load_selected_region()
        self.load_selected_probe()
        if self.parent() and hasattr(self.parent(), "capture_once"):
            self.parent().capture_once(force=True)


class MainWindow(QMainWindow):
    refresh_interval_ms = 333

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("MahjongMaster - Monitor de Janela")
        self.resize(600, 600)
        self.screen_capture = mss.MSS()
        self.last_capture: QImage | None = None
        self.prediction_model = None
        self.prediction_model_path: Path | None = None
        self.predict_runtime_error: str | None = None
        self.app_config = load_config(CONFIG_PATH)
        save_config(CONFIG_PATH, self.app_config)
        self.capture_count = 0
        self.predict_count = 0
        self.last_game_summary = "Hand: - | Yaku provavel: -"
        self.auto_turn_frames = 0
        self.auto_decision_history: dict[str, deque[bool]] = {
            action: deque(maxlen=3)
            for action in ("Chii", "Pon", "Kan", "Riichi", "Ron/Tsumo")
        }
        self.auto_last_click_at = 0.0
        self.auto_click_pending = False
        self.auto_pending_context: dict | None = None
        self.auto_blocked_discard_keys: dict[str, float] = {}
        self.auto_last_discard_attempt: dict | None = None
        self.latest_auto_image: QImage | None = None
        self.latest_auto_detections: list = []
        self.latest_auto_state: HandState | None = None
        self.latest_auto_riichi_button_visible = False
        self.latest_auto_win_button_visible = False
        self.auto_mouse_delay_seconds = (
            float(self.app_config.get("auto_mouse_delay_min", 0.4)),
            float(self.app_config.get("auto_mouse_delay_max", 1.2)),
        )
        self.auto_click_delay_seconds = (
            float(self.app_config.get("auto_click_delay_min", 3.0)),
            float(self.app_config.get("auto_click_delay_max", 5.0)),
        )
        self.annotator_window: AnnotatorWindow | None = None
        self.training_window = None
        self.region_config_dialog: RegionConfigDialog | None = None

        self.window_combo = QComboBox()
        self.window_combo.setMinimumWidth(90)
        self.window_combo.currentIndexChanged.connect(self.capture_once)

        self.refresh_button = QToolButton()
        self.refresh_button.setToolTip("Atualizar janelas")
        self.refresh_button.setIcon(QIcon(str(ASSETS_DIR / "icons" / "refresh.svg")))
        self.refresh_button.clicked.connect(self.refresh_windows)

        self.save_button = QPushButton("Salvar screenshot (F3)")
        self.save_button.clicked.connect(self.save_screenshot)

        self.annotator_button = QPushButton("Abrir anotador")
        self.annotator_button.clicked.connect(self.open_annotator)

        self.training_button = QPushButton("Abrir treino")
        self.training_button.clicked.connect(self.open_training)

        self.region_config_button = QPushButton("Areas")
        self.region_config_button.clicked.connect(self.open_region_config)

        self.model_combo = QComboBox()
        self.model_combo.setMinimumWidth(90)
        self.model_combo.currentIndexChanged.connect(self.unload_prediction_model)

        self.predict_checkbox = QCheckBox("Pred")
        self.predict_checkbox.stateChanged.connect(self.predict_setting_changed)

        self.predict_once_button = QPushButton("Predict 1x")
        self.predict_once_button.clicked.connect(self.predict_once_clicked)

        self.predict_fps_input = QDoubleSpinBox()
        self.predict_fps_input.setRange(0.2, 60.0)
        self.predict_fps_input.setSingleStep(0.5)
        self.predict_fps_input.setValue(float(self.app_config.get("predict_fps", 30.0)))
        self.predict_fps_input.setSuffix(" FPS")
        self.predict_fps_input.setFixedWidth(86)
        self.predict_fps_input.valueChanged.connect(self.predict_setting_changed)

        self.predict_conf_input = QDoubleSpinBox()
        self.predict_conf_input.setRange(0.01, 0.99)
        self.predict_conf_input.setSingleStep(0.05)
        self.predict_conf_input.setValue(float(self.app_config.get("predict_conf", 0.50)))
        self.predict_conf_input.setFixedWidth(58)
        self.predict_conf_input.valueChanged.connect(self.predict_parameter_changed)

        self.pause_after_predict_checkbox = QCheckBox("Pausar apos proximo predict")

        self.auto_play_checkbox = QCheckBox("Auto")
        self.auto_play_checkbox.setToolTip("Tab liga/desliga. Espera 3 frames da sua vez e clica no descarte recomendado.")
        self.auto_play_checkbox.stateChanged.connect(self.auto_play_setting_changed)

        self.preview_checkbox = QCheckBox("Prev")
        self.preview_checkbox.setChecked(True)
        self.preview_checkbox.stateChanged.connect(self.preview_setting_changed)

        self.debug_checkbox = QCheckBox("Dbg")
        self.debug_checkbox.setChecked(bool(self.app_config.get("debug_enabled", False)))
        self.debug_checkbox.stateChanged.connect(self.debug_setting_changed)

        self.auto_mouse_min_input = QDoubleSpinBox()
        self.auto_mouse_min_input.setRange(0.0, 30.0)
        self.auto_mouse_min_input.setSingleStep(0.1)
        self.auto_mouse_min_input.setDecimals(1)
        self.auto_mouse_min_input.setSuffix(" s")
        self.auto_mouse_min_input.setFixedWidth(76)
        self.auto_mouse_min_input.setValue(float(self.app_config.get("auto_mouse_delay_min", 0.4)))
        self.auto_mouse_min_input.valueChanged.connect(self.auto_timing_changed)

        self.auto_mouse_max_input = QDoubleSpinBox()
        self.auto_mouse_max_input.setRange(0.0, 30.0)
        self.auto_mouse_max_input.setSingleStep(0.1)
        self.auto_mouse_max_input.setDecimals(1)
        self.auto_mouse_max_input.setSuffix(" s")
        self.auto_mouse_max_input.setFixedWidth(76)
        self.auto_mouse_max_input.setValue(float(self.app_config.get("auto_mouse_delay_max", 1.2)))
        self.auto_mouse_max_input.valueChanged.connect(self.auto_timing_changed)

        self.auto_click_min_input = QDoubleSpinBox()
        self.auto_click_min_input.setRange(0.1, 30.0)
        self.auto_click_min_input.setSingleStep(0.1)
        self.auto_click_min_input.setDecimals(1)
        self.auto_click_min_input.setSuffix(" s")
        self.auto_click_min_input.setFixedWidth(76)
        self.auto_click_min_input.setValue(float(self.app_config.get("auto_click_delay_min", 3.0)))
        self.auto_click_min_input.valueChanged.connect(self.auto_timing_changed)

        self.auto_click_max_input = QDoubleSpinBox()
        self.auto_click_max_input.setRange(0.1, 30.0)
        self.auto_click_max_input.setSingleStep(0.1)
        self.auto_click_max_input.setDecimals(1)
        self.auto_click_max_input.setSuffix(" s")
        self.auto_click_max_input.setFixedWidth(76)
        self.auto_click_max_input.setValue(float(self.app_config.get("auto_click_delay_max", 5.0)))
        self.auto_click_max_input.valueChanged.connect(self.auto_timing_changed)

        self.options_button = QToolButton()
        self.options_button.setText("Opcoes")
        self.options_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.options_button.setMenu(self.build_options_menu())

        self.show_regions_checkbox = QCheckBox("Areas")
        self.show_regions_checkbox.setChecked(bool(self.app_config.get("show_regions_overlay", True)))
        self.show_regions_checkbox.stateChanged.connect(self.show_regions_changed)

        self.preview_label = QLabel("Selecione uma janela aberta para iniciar a preview.")
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setMinimumSize(160, 100)
        self.preview_label.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        self.preview_label.setStyleSheet(
            "QLabel { background: #0B1220; color: #E5E7EB; border: 1px solid #374151; }"
        )

        self.debug_state_label = QLabel("Capturas: 0 | Predicts: 0")
        self.debug_state_label.setVisible(False)
        self.game_summary_log = QPlainTextEdit()
        self.game_summary_log.setReadOnly(True)
        self.game_summary_log.setMinimumHeight(120)
        self.game_summary_log.document().setMaximumBlockCount(250)
        self.game_summary_log.setPlaceholderText("Resumo da mao aparece aqui apos o predict.")

        self.debug_log = QPlainTextEdit()
        self.debug_log.setReadOnly(True)
        self.debug_log.setMaximumHeight(95)
        self.debug_log.document().setMaximumBlockCount(250)
        self.debug_log.setPlaceholderText("Logs tecnicos aparecem aqui com DEBUG ligado.")
        self.debug_log.setVisible(False)

        self.save_button.setText("Shot F3")
        self.annotator_button.setText("Anotar")
        self.training_button.setText("Treino")
        self.annotator_button.setMaximumWidth(58)
        self.training_button.setMaximumWidth(58)
        self.region_config_button.setMaximumWidth(54)

        controls = QHBoxLayout()
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setSpacing(4)
        controls.addWidget(QLabel("Jan:"))
        controls.addWidget(self.window_combo, stretch=3)
        controls.addWidget(self.refresh_button)
        controls.addWidget(QLabel("Mod:"))
        controls.addWidget(self.model_combo, stretch=4)
        controls.addWidget(self.predict_checkbox)
        controls.addWidget(self.auto_play_checkbox)
        controls.addWidget(self.preview_checkbox)
        controls.addWidget(self.show_regions_checkbox)
        controls.addWidget(self.options_button)
        controls.addWidget(self.annotator_button)
        controls.addWidget(self.training_button)
        controls.addWidget(self.region_config_button)
        f3_label = QLabel("F3 salva")
        f3_label.setStyleSheet("QLabel { color: #94A3B8; font-size: 10px; }")
        controls.addWidget(f3_label)

        root_layout = QVBoxLayout()
        root_layout.setContentsMargins(4, 4, 4, 4)
        root_layout.setSpacing(4)
        root_layout.addLayout(controls)
        self.preview_summary_splitter = QSplitter(Qt.Orientation.Vertical)
        self.preview_summary_splitter.addWidget(self.preview_label)
        self.preview_summary_splitter.addWidget(self.game_summary_log)
        self.preview_summary_splitter.setStretchFactor(0, 3)
        self.preview_summary_splitter.setStretchFactor(1, 2)
        self.preview_summary_splitter.setSizes([360, 240])

        root_layout.addWidget(self.preview_summary_splitter, stretch=1)
        root_layout.addWidget(self.debug_state_label)
        root_layout.addWidget(self.debug_log)

        root = QWidget()
        root.setLayout(root_layout)
        self.setCentralWidget(root)
        self.setStatusBar(QStatusBar())

        self.timer = QTimer(self)
        self.timer.setInterval(self.refresh_interval_ms)
        self.timer.timeout.connect(self.capture_once)

        self.refresh_model_list()
        self.refresh_windows()
        self.refresh_timer_interval()
        self.timer.start()
        self.set_game_summary(self.last_game_summary)
        self.debug_setting_changed()
        self.log_debug("App iniciado.")

    def build_options_menu(self) -> QMenu:
        menu = QMenu(self)
        panel = QWidget(menu)
        layout = QFormLayout(panel)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(6)
        layout.addRow("FPS", self.predict_fps_input)
        layout.addRow("Confianca", self.predict_conf_input)
        layout.addRow("Debug", self.debug_checkbox)
        layout.addRow("Mover min", self.auto_mouse_min_input)
        layout.addRow("Mover max", self.auto_mouse_max_input)
        layout.addRow("Clique min", self.auto_click_min_input)
        layout.addRow("Clique max", self.auto_click_max_input)
        action = QWidgetAction(menu)
        action.setDefaultWidget(panel)
        menu.addAction(action)
        return menu

    def refresh_windows(self) -> None:
        current_hwnd = self.selected_hwnd()
        windows = list_visible_windows()

        self.window_combo.blockSignals(True)
        self.window_combo.clear()
        for window in windows:
            self.window_combo.addItem(window.label, window.hwnd)

        selected_index = -1
        if current_hwnd is not None:
            selected_index = self.window_combo.findData(current_hwnd)

        if selected_index < 0:
            selected_index = self.default_window_index()

        if selected_index >= 0:
            self.window_combo.setCurrentIndex(selected_index)

        self.window_combo.blockSignals(False)
        self.statusBar().showMessage(f"{len(windows)} janelas visiveis encontradas.")
        self.capture_once()

    def default_window_index(self) -> int:
        target = DEFAULT_WINDOW_TITLE.lower()
        for index in range(self.window_combo.count()):
            label = self.window_combo.itemText(index).lower()
            if target in label:
                return index

        return 0 if self.window_combo.count() else -1

    def selected_hwnd(self) -> int | None:
        hwnd = self.window_combo.currentData()
        return int(hwnd) if hwnd is not None else None

    def log_debug(self, message: str) -> None:
        if not self.debug_checkbox.isChecked():
            return
        timestamp = datetime.now().strftime("%H:%M:%S")
        line = f"[{timestamp}] {message}"
        print(line, flush=True)
        self.debug_log.appendPlainText(line)
        self.debug_log.repaint()

    def set_game_summary(self, summary: str) -> None:
        self.last_game_summary = summary
        self.game_summary_log.setPlainText(summary)
        self.game_summary_log.repaint()

    def debug_setting_changed(self, *_args) -> None:
        enabled = self.debug_checkbox.isChecked()
        self.app_config["debug_enabled"] = enabled
        save_config(CONFIG_PATH, self.app_config)
        self.debug_state_label.setVisible(enabled)
        self.debug_log.setVisible(enabled)
        if enabled:
            self.debug_log.appendPlainText("[DEBUG ligado]")
        else:
            self.debug_log.clear()

    def show_regions_changed(self, *_args) -> None:
        self.app_config["show_regions_overlay"] = self.show_regions_checkbox.isChecked()
        save_config(CONFIG_PATH, self.app_config)
        self.capture_once(force=True)

    def refresh_debug_state(self) -> None:
        state = "ON" if self.predict_checkbox.isChecked() else "OFF"
        auto_state = "ON" if self.auto_play_checkbox.isChecked() else "OFF"
        self.debug_state_label.setText(
            f"Capturas: {self.capture_count} | Predicts: {self.predict_count} | Predict: {state} | Auto: {auto_state} | "
            f"Timer: {self.timer.interval()} ms"
        )

    def capture_once(self, *_args, force: bool = False) -> None:
        if not force and not self.preview_checkbox.isChecked() and not self.predict_checkbox.isChecked():
            self.preview_label.setPixmap(QPixmap())
            self.preview_label.setText("Preview desabilitado. Ative Preview ou Predict para monitorar.")
            self.refresh_debug_state()
            return

        hwnd = self.selected_hwnd()
        if hwnd is None:
            self.last_capture = None
            self.preview_label.setText("Nenhuma janela disponivel.")
            self.preview_label.setPixmap(QPixmap())
            self.refresh_debug_state()
            return

        bounds = window_bounds(hwnd)
        if bounds is None:
            self.last_capture = None
            self.preview_label.setText("Nao foi possivel localizar a area desta janela.")
            self.preview_label.setPixmap(QPixmap())
            self.log_debug(f"Captura falhou: bounds indisponiveis para HWND 0x{hwnd:08X}.")
            self.refresh_debug_state()
            return

        monitor = clamp_to_monitor(bounds, self.screen_capture.monitors)
        if monitor is None:
            self.last_capture = None
            self.preview_label.setText("A janela selecionada esta fora dos monitores capturaveis.")
            self.preview_label.setPixmap(QPixmap())
            self.log_debug(f"Captura falhou: HWND 0x{hwnd:08X} fora dos monitores.")
            self.refresh_debug_state()
            return

        try:
            screenshot = self.screen_capture.grab(monitor)
        except Exception as error:
            self.last_capture = None
            self.preview_label.setText(
                "Nao foi possivel capturar esta janela. "
                "Verifique se ela nao esta minimizada ou em fullscreen exclusivo."
            )
            self.preview_label.setPixmap(QPixmap())
            message = f"Falha na captura: {type(error).__name__}: {error}"
            self.log_debug(message)
            self.statusBar().showMessage(message)
            self.refresh_debug_state()
            return

        image = QImage(
            screenshot.bgra,
            screenshot.width,
            screenshot.height,
            screenshot.width * 4,
            QImage.Format.Format_RGB32,
        ).copy()
        image = self.normalized_capture_image(image)
        self.last_capture = image
        self.capture_count += 1
        display_image = self.predicted_image(image)
        if self.show_regions_checkbox.isChecked():
            display_image = display_image.copy()
            self.draw_detection_regions(display_image)
        if self.preview_checkbox.isChecked():
            pixmap = QPixmap.fromImage(display_image)
            scaled = pixmap.scaled(
                self.preview_label.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self.preview_label.setPixmap(scaled)
        else:
            self.preview_label.setPixmap(QPixmap())
            self.preview_label.setText("Preview desabilitado. Predict continua rodando em segundo plano.")

        if not self.predict_checkbox.isChecked():
            self.statusBar().showMessage(
                f"Monitorando HWND 0x{hwnd:08X} a {self.predict_fps_input.value():.1f} Hz | "
                f"captura {CAPTURE_WIDTH}x{CAPTURE_HEIGHT}"
            )
        self.refresh_debug_state()

    def refresh_timer_interval(self) -> None:
        interval = max(1, int(1000 / self.predict_fps_input.value()))
        self.timer.setInterval(interval)
        self.app_config["predict_fps"] = self.predict_fps_input.value()
        save_config(CONFIG_PATH, self.app_config)
        self.refresh_debug_state()

    def predict_setting_changed(self, *_args) -> None:
        if self.predict_checkbox.isChecked() and self.selected_model_path() is None:
            self.predict_checkbox.setChecked(False)
            if self.auto_play_checkbox.isChecked():
                self.auto_play_checkbox.setChecked(False)
            message = "Nenhum modelo best.pt disponivel para predict."
            self.preview_label.setPixmap(QPixmap())
            self.preview_label.setText(message)
            self.log_debug(message)
            self.statusBar().showMessage(message)
            return
        self.refresh_timer_interval()
        if self.predict_checkbox.isChecked():
            self.predict_runtime_error = None
            self.log_debug(
                f"Predict ligado | modelo={self.model_combo.currentText()} | "
                f"imgsz={PREDICT_IMAGE_SIZE} | conf={self.predict_conf_input.value():.2f}"
            )
            self.statusBar().showMessage(f"Predict ligado: {self.model_combo.currentText()}")
            self.capture_once()
        else:
            self.log_debug("Predict desligado.")

    def auto_play_setting_changed(self, *_args) -> None:
        self.auto_turn_frames = 0
        for history in self.auto_decision_history.values():
            history.clear()
        if self.auto_play_checkbox.isChecked() and not self.predict_checkbox.isChecked():
            self.predict_checkbox.setChecked(True)
        state = "ligado" if self.auto_play_checkbox.isChecked() else "desligado"
        self.log_debug(f"Auto {state}.")
        self.refresh_debug_state()

    def toggle_auto_play(self) -> None:
        self.auto_play_checkbox.setChecked(not self.auto_play_checkbox.isChecked())
        state = "ligado" if self.auto_play_checkbox.isChecked() else "desligado"
        self.statusBar().showMessage(f"AutoPlay {state} pelo Tab.")

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Tab:
            self.toggle_auto_play()
            event.accept()
            return
        super().keyPressEvent(event)

    def predict_parameter_changed(self, *_args) -> None:
        self.predict_runtime_error = None
        self.app_config["predict_conf"] = self.predict_conf_input.value()
        save_config(CONFIG_PATH, self.app_config)
        self.log_debug(f"Parametros alterados | conf={self.predict_conf_input.value():.2f}")
        if self.predict_checkbox.isChecked():
            self.capture_once()

    def auto_timing_changed(self, *_args) -> None:
        mouse_min, mouse_max = self.sync_timing_pair(
            self.auto_mouse_min_input,
            self.auto_mouse_max_input,
        )
        click_min, click_max = self.sync_timing_pair(
            self.auto_click_min_input,
            self.auto_click_max_input,
        )
        self.auto_mouse_delay_seconds = (mouse_min, mouse_max)
        self.auto_click_delay_seconds = (click_min, click_max)
        self.app_config["auto_mouse_delay_min"] = mouse_min
        self.app_config["auto_mouse_delay_max"] = mouse_max
        self.app_config["auto_click_delay_min"] = click_min
        self.app_config["auto_click_delay_max"] = click_max
        save_config(CONFIG_PATH, self.app_config)
        self.statusBar().showMessage(
            f"Auto: move {mouse_min:.1f}-{mouse_max:.1f}s; clique {click_min:.1f}-{click_max:.1f}s."
        )

    def sync_timing_pair(self, min_input: QDoubleSpinBox, max_input: QDoubleSpinBox) -> tuple[float, float]:
        minimum = min_input.value()
        maximum = max_input.value()
        if minimum <= maximum:
            return minimum, maximum

        sender = self.sender()
        if sender is min_input:
            max_input.blockSignals(True)
            max_input.setValue(minimum)
            max_input.blockSignals(False)
            return minimum, minimum

        min_input.blockSignals(True)
        min_input.setValue(maximum)
        min_input.blockSignals(False)
        return maximum, maximum

    def preview_setting_changed(self, *_args) -> None:
        if self.preview_checkbox.isChecked():
            self.log_debug("Preview ligado.")
            self.capture_once()
        else:
            self.log_debug("Preview desligado.")
            self.preview_label.setPixmap(QPixmap())
            self.preview_label.setText("Preview desabilitado. Ative novamente para ver a janela.")
        self.refresh_debug_state()

    def refresh_model_list(self) -> None:
        current_path = self.selected_model_path()
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        paths = self.available_model_paths()
        for path in paths:
            label = path.relative_to(PROJECT_ROOT).as_posix()
            self.model_combo.addItem(label, str(path))

        if not paths:
            self.model_combo.addItem("Nenhum best.pt encontrado", "")

        if current_path is not None:
            index = self.model_combo.findData(str(current_path))
            if index >= 0:
                self.model_combo.setCurrentIndex(index)
        elif paths:
            self.model_combo.setCurrentIndex(0)

        self.model_combo.blockSignals(False)
        self.unload_prediction_model()

    def available_model_paths(self) -> list[Path]:
        if not RUNS_DIR.exists():
            return []
        return sorted(RUNS_DIR.glob("*/weights/best.pt"), key=lambda path: path.stat().st_mtime, reverse=True)

    def selected_model_path(self) -> Path | None:
        data = self.model_combo.currentData()
        return Path(data) if data else None

    def unload_prediction_model(self, *_args) -> None:
        self.prediction_model = None
        self.prediction_model_path = None
        self.predict_runtime_error = None
        if hasattr(self, "debug_log"):
            self.log_debug(f"Modelo selecionado: {self.model_combo.currentText()}")

    def normalized_capture_image(self, image: QImage) -> QImage:
        if image.width() == CAPTURE_WIDTH and image.height() == CAPTURE_HEIGHT:
            return image

        scaled = image.scaled(
            CAPTURE_WIDTH,
            CAPTURE_HEIGHT,
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        )
        left = max(0, (scaled.width() - CAPTURE_WIDTH) // 2)
        top = max(0, (scaled.height() - CAPTURE_HEIGHT) // 2)
        return scaled.copy(left, top, CAPTURE_WIDTH, CAPTURE_HEIGHT)

    def prediction_device(self) -> str:
        try:
            import torch

            if torch.cuda.is_available() and torch.cuda.device_count() > 0:
                return "0"
        except Exception as error:
            self.log_debug(f"Nao foi possivel consultar CUDA: {type(error).__name__}: {error}")
        return "cpu"

    def load_prediction_model(self):
        model_path = self.selected_model_path()
        if model_path is None:
            self.log_debug("Load cancelado: nenhum modelo selecionado.")
            return None
        if self.prediction_model is not None and self.prediction_model_path == model_path:
            return self.prediction_model

        try:
            from ultralytics import RTDETR, YOLO

            started_at = time.perf_counter()
            self.log_debug(f"Carregando modelo: {model_path}")
            is_rtdetr = any("rtdetr" in part.lower() for part in model_path.parts)
            self.prediction_model = RTDETR(str(model_path)) if is_rtdetr else YOLO(str(model_path))
            self.prediction_model_path = model_path
            elapsed_ms = (time.perf_counter() - started_at) * 1000
            class_count = len(getattr(self.prediction_model, "names", {}) or {})
            message = f"Modelo carregado: {model_path.name} | {class_count} classes | {elapsed_ms:.0f} ms"
            self.log_debug(message)
            self.statusBar().showMessage(message)
            return self.prediction_model
        except Exception as error:
            self.prediction_model = None
            self.prediction_model_path = None
            message = f"Falha ao carregar modelo: {type(error).__name__}: {error}"
            self.predict_runtime_error = message
            self.preview_label.setPixmap(QPixmap())
            self.preview_label.setText(message)
            self.log_debug(message)
            self.statusBar().showMessage(message)
            return None

    def predicted_image(self, image: QImage) -> QImage:
        if not self.predict_checkbox.isChecked():
            return image

        if self.predict_runtime_error:
            return image

        return self.run_prediction(image, source="timer")

    def predict_once_clicked(self) -> None:
        self.log_debug("Predict 1x solicitado.")
        if self.last_capture is None:
            self.capture_once(force=True)

        if self.last_capture is None:
            message = "Predict 1x cancelado: nenhum frame capturado."
            self.log_debug(message)
            self.statusBar().showMessage(message)
            return

        self.predict_runtime_error = None
        output = self.run_prediction(self.last_capture, source="manual")
        if self.preview_checkbox.isChecked():
            pixmap = QPixmap.fromImage(output)
            scaled = pixmap.scaled(
                self.preview_label.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self.preview_label.setPixmap(scaled)
        else:
            self.preview_label.setPixmap(QPixmap.fromImage(output).scaled(
                self.preview_label.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            ))
        self.refresh_debug_state()

    def run_prediction(self, image: QImage, source: str) -> QImage:
        model = self.load_prediction_model()
        if model is None:
            return image

        try:
            started_at = time.perf_counter()
            device = self.prediction_device()
            chii_button_visible = self.detect_chii_button(image)
            pon_button_visible = self.detect_pon_button(image)
            kan_button_visible = self.detect_kan_button(image)
            riichi_button_visible = self.detect_riichi_button(image)
            win_button_visible = self.detect_win_button(image)
            call_source_player = "esquerda" if chii_button_visible else self.detect_call_source_player(image)
            player_winds = self.detect_player_winds(image)
            self.log_debug(
                f"Predict start ({source}) | frame={image.width()}x{image.height()} | "
                f"imgsz={PREDICT_IMAGE_SIZE} | conf={self.predict_conf_input.value():.2f} | device={device}"
            )
            result = model.predict(
                source=self.qimage_to_rgb_array(image),
                imgsz=PREDICT_IMAGE_SIZE,
                conf=self.predict_conf_input.value(),
                device=device,
                verbose=False,
            )[0]
            elapsed_ms = (time.perf_counter() - started_at) * 1000
        except Exception as error:
            message = f"Falha no predict: {type(error).__name__}: {error}"
            self.predict_runtime_error = message
            self.log_debug(message)
            self.statusBar().showMessage(message)
            if self.preview_checkbox.isChecked():
                self.preview_label.setText(message)
            return image

        detections = detections_from_yolo_result(result)
        state = GameAnalyzer(CAPTURE_WIDTH, CAPTURE_HEIGHT, self.app_config["regions"]).analyze(
            detections,
            chii_button_visible=chii_button_visible,
            pon_button_visible=pon_button_visible,
            kan_button_visible=kan_button_visible,
            call_source_player=call_source_player,
            player_winds=player_winds,
        )
        self.extend_terminal_action_decisions(state, riichi_button_visible, win_button_visible)
        self.update_game_state(state)
        output = image.copy()
        self.draw_predictions(output, result, state)
        self.draw_call_indicators(output, state, call_source_player)
        self.handle_auto_play(image, detections, state, riichi_button_visible, win_button_visible)
        self.predict_count += 1
        self.log_debug(f"Predict ok ({source}) | {len(result.boxes)} deteccoes | {elapsed_ms:.0f} ms")
        self.statusBar().showMessage(
            f"Predict: {len(result.boxes)} deteccoes | {elapsed_ms:.0f} ms | "
            f"conf {self.predict_conf_input.value():.2f} | imgsz {PREDICT_IMAGE_SIZE}"
        )

        if self.pause_after_predict_checkbox.isChecked():
            self.predict_checkbox.setChecked(False)
            self.pause_after_predict_checkbox.setChecked(False)
            self.statusBar().showMessage(
                f"Predict pausado apos 1 frame: {len(result.boxes)} deteccoes | {elapsed_ms:.0f} ms"
            )

        return output

    def update_game_state(self, state: HandState) -> None:
        self.set_game_summary(state.summary())
        self.log_debug(f"Estado de jogo: {state.summary()}")

    def extend_terminal_action_decisions(
        self,
        state: HandState,
        riichi_button_visible: bool,
        win_button_visible: bool,
    ) -> None:
        state.riichi_button_visible = riichi_button_visible
        state.win_button_visible = win_button_visible
        if riichi_button_visible:
            recommended = state.is_closed
            state.call_decisions.append(
                CallDecision(
                    "Riichi",
                    recommended,
                    95 if recommended else 0,
                    "botao visivel",
                    "tenpai detectado pela UI" if recommended else "mao aberta",
                )
            )
        if win_button_visible:
            state.call_decisions.append(
                CallDecision(
                    "Ron/Tsumo",
                    True,
                    100,
                    "botao visivel",
                    "vitoria disponivel",
                )
            )

    def detect_chii_button(self, image: QImage) -> bool:
        return self.active_button_probe(image, "Chii") is not None

    def detect_pon_button(self, image: QImage) -> bool:
        return self.active_button_probe(image, "Pon") is not None

    def detect_kan_button(self, image: QImage) -> bool:
        return self.active_button_probe(image, "Kan") is not None

    def detect_riichi_button(self, image: QImage) -> bool:
        return self.active_button_probe(image, "Riichi") is not None

    def detect_win_button(self, image: QImage) -> bool:
        return self.active_button_probe(image, "Ron/Tsumo") is not None

    @staticmethod
    def is_chii_green(color: QColor) -> bool:
        hue = color.hsvHue()
        return (
            85 <= hue <= 150
            and color.saturation() >= 70
            and color.value() >= 120
            and color.green() > color.red() * 1.35
            and color.green() > color.blue() * 1.08
        )

    @staticmethod
    def is_pon_blue(color: QColor) -> bool:
        hue = color.hsvHue()
        return (
            170 <= hue <= 205
            and color.saturation() >= 85
            and color.value() >= 135
            and color.blue() >= 120
            and color.green() >= 115
            and color.red() <= 85
            and color.green() <= color.blue() * 1.25
            and color.blue() <= color.green() * 1.45
        )

    def detect_call_source_player(self, image: QImage) -> str | None:
        probes = {
            "esquerda": "turn_left",
            "direita": "turn_right",
            "cima": "turn_top",
            "principal": "turn_player",
        }
        for player, key in probes.items():
            if self.probe_matches(image, key):
                return player
        return None

    def detect_own_turn(self, image: QImage) -> bool:
        return self.probe_matches(image, "turn_player")

    def handle_auto_play(
        self,
        image: QImage,
        detections: list,
        state: HandState,
        riichi_button_visible: bool,
        win_button_visible: bool,
    ) -> None:
        self.remember_auto_context(image, detections, state, riichi_button_visible, win_button_visible)
        if not self.auto_play_checkbox.isChecked():
            self.auto_turn_frames = 0
            self.auto_last_discard_attempt = None
            self.auto_blocked_discard_keys.clear()
            for history in self.auto_decision_history.values():
                history.clear()
            return
        self.update_auto_discard_failure_guard(state)
        if self.auto_click_pending or time.perf_counter() - self.auto_last_click_at < 0.8:
            return

        visible_actions = {
            "Chii": state.chii_button_visible,
            "Pon": state.pon_button_visible,
            "Kan": state.kan_button_visible,
            "Riichi": riichi_button_visible,
            "Ron/Tsumo": win_button_visible,
        }
        recommended_actions = {
            decision.action
            for decision in state.call_decisions
            if decision.recommended
        }
        for action, visible in visible_actions.items():
            self.auto_decision_history[action].append(bool(visible and action in recommended_actions))

        for action in ("Ron/Tsumo", "Riichi", "Kan", "Pon", "Chii"):
            if visible_actions[action] and any(self.auto_decision_history[action]):
                if self.click_action_button(action):
                    self.log_debug(f"Auto: agendou clique em {action}.")
                    self.auto_last_click_at = time.perf_counter()
                    self.auto_turn_frames = 0
                return

        if self.detect_own_turn(image):
            self.auto_turn_frames += 1
        else:
            self.auto_turn_frames = 0

        if self.auto_turn_frames < 3:
            return

        if self.click_best_discard(detections, state):
            self.log_debug("Auto: agendou descarte da pior peca recomendada.")
            self.auto_last_click_at = time.perf_counter()
            self.auto_turn_frames = 0

    def remember_auto_context(
        self,
        image: QImage,
        detections: list,
        state: HandState,
        riichi_button_visible: bool,
        win_button_visible: bool,
    ) -> None:
        self.latest_auto_image = image.copy()
        self.latest_auto_detections = list(detections)
        self.latest_auto_state = state
        self.latest_auto_riichi_button_visible = riichi_button_visible
        self.latest_auto_win_button_visible = win_button_visible

    def update_auto_discard_failure_guard(self, state: HandState) -> None:
        attempt = self.auto_last_discard_attempt
        if not attempt:
            self.clear_expired_auto_discard_blocks()
            return

        now = time.perf_counter()
        target_key = str(attempt.get("tile_key", ""))
        if not target_key:
            self.auto_last_discard_attempt = None
            return

        still_in_hand = any(tile.base_key == target_key for tile in state.hand_tiles)
        if not still_in_hand:
            self.auto_last_discard_attempt = None
            self.auto_blocked_discard_keys.pop(target_key, None)
            return

        still_best = bool(state.discard_candidates and state.discard_candidates[0].base_key == target_key)
        if not still_best:
            self.auto_last_discard_attempt = None
            return

        attempt["frames"] = int(attempt.get("frames", 0)) + 1
        elapsed = now - float(attempt.get("at", now))
        if attempt["frames"] >= 3 or elapsed >= 1.4:
            blocked_for_seconds = 12.0
            self.auto_blocked_discard_keys[target_key] = now + blocked_for_seconds
            self.auto_last_discard_attempt = None
            self.auto_turn_frames = 0
            self.log_debug(
                f"Auto: {attempt.get('tile_label', target_key)} continuou na mao apos clique; "
                f"bloqueada por {blocked_for_seconds:.0f}s."
            )

    def clear_expired_auto_discard_blocks(self) -> None:
        if not self.auto_blocked_discard_keys:
            return
        now = time.perf_counter()
        expired = [key for key, expires_at in self.auto_blocked_discard_keys.items() if expires_at <= now]
        for key in expired:
            self.auto_blocked_discard_keys.pop(key, None)

    def click_action_button(self, action: str) -> bool:
        probe = self.active_button_probe(self.latest_auto_image or self.last_capture, action)
        if probe is None:
            return False
        return self.click_normalized_point(
            float(probe["x"]),
            float(probe["y"]),
            {"kind": "action", "action": action},
        )

    def click_best_discard(self, detections: list, state: HandState) -> bool:
        discard_tile = self.next_auto_discard_candidate(state)
        if discard_tile is None:
            return False
        target_key = discard_tile.base_key
        player_region = self.configured_region_rect("player_hand")
        candidates = []
        for detection in detections:
            tile = tile_from_name(detection.name)
            if tile is None or tile.base_key != target_key:
                continue
            if not player_region.contains(QPointF(detection.center_x, detection.center_y)):
                continue
            if not self.detection_crosses_player_closed_line(detection.y1, detection.y2):
                continue
            candidates.append(detection)
        if not candidates:
            self.log_debug(f"Auto: nao encontrou box clicavel para {discard_tile.compact}.")
            return False

        target = max(candidates, key=lambda item: item.center_y)
        return self.click_normalized_point(
            target.center_x,
            target.center_y,
            {"kind": "discard", "tile_key": target_key, "tile_label": discard_tile.compact},
        )

    def next_auto_discard_candidate(self, state: HandState) -> Tile | None:
        if not state.discard_candidates:
            return None

        self.clear_expired_auto_discard_blocks()
        for tile in state.discard_candidates:
            if tile.base_key not in self.auto_blocked_discard_keys:
                return tile

        blocked = ", ".join(tile.compact for tile in state.discard_candidates[:4])
        self.log_debug(f"Auto: descartes recomendados temporariamente bloqueados: {blocked}.")
        return None

    def click_normalized_point(self, x: float, y: float, context: dict | None = None) -> bool:
        if self.auto_click_pending:
            return False
        hwnd = self.selected_hwnd()
        if hwnd is None:
            return False
        bounds = window_bounds(hwnd)
        if bounds is None:
            return False
        monitor = clamp_to_monitor(bounds, self.screen_capture.monitors)
        if monitor is None:
            return False

        scale = max(CAPTURE_WIDTH / monitor["width"], CAPTURE_HEIGHT / monitor["height"])
        scaled_width = monitor["width"] * scale
        scaled_height = monitor["height"] * scale
        crop_x = max(0.0, (scaled_width - CAPTURE_WIDTH) / 2)
        crop_y = max(0.0, (scaled_height - CAPTURE_HEIGHT) / 2)
        screen_x = int(monitor["left"] + (x + crop_x) / scale)
        screen_y = int(monitor["top"] + (y + crop_y) / scale)

        move_delay_seconds = random.uniform(*self.auto_mouse_delay_seconds)
        move_delay_ms = round(move_delay_seconds * 1000)
        self.auto_click_pending = True
        self.auto_pending_context = context
        self.statusBar().showMessage(f"Auto: vai mover o mouse em {move_delay_seconds:.1f}s.")
        QTimer.singleShot(
            move_delay_ms,
            lambda: self.finish_scheduled_mouse_move(hwnd, screen_x, screen_y),
        )
        return True

    def finish_scheduled_mouse_move(self, hwnd: int, screen_x: int, screen_y: int) -> None:
        if not self.auto_play_checkbox.isChecked():
            self.statusBar().showMessage("Auto: movimento cancelado porque AutoPlay foi desligado.")
            self.log_debug("Auto: movimento pendente cancelado.")
            self.auto_click_pending = False
            self.auto_pending_context = None
            return

        user32.SetForegroundWindow(hwnd)
        user32.SetCursorPos(screen_x, screen_y)
        click_delay_seconds = random.uniform(*self.auto_click_delay_seconds)
        click_delay_ms = round(click_delay_seconds * 1000)
        self.statusBar().showMessage(f"Auto: cursor posicionado; clique em {click_delay_seconds:.1f}s.")
        self.log_debug(
            f"Auto: mouse movido; clique agendado em {click_delay_seconds:.1f}s."
        )
        QTimer.singleShot(click_delay_ms, self.finish_scheduled_click)

    def finish_scheduled_click(self) -> None:
        restarted = False
        context = dict(self.auto_pending_context or {})
        try:
            if not self.auto_play_checkbox.isChecked():
                self.statusBar().showMessage("Auto: clique cancelado porque AutoPlay foi desligado.")
                self.log_debug("Auto: clique pendente cancelado.")
                return
            if not self.pending_click_still_valid():
                self.statusBar().showMessage("Auto: alvo mudou; reiniciando rotina.")
                self.log_debug("Auto: alvo pendente mudou antes do clique; reiniciando.")
                self.restart_auto_from_latest_context()
                restarted = True
                return
            user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
            user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
            self.auto_last_click_at = time.perf_counter()
            if context.get("kind") == "discard":
                self.auto_last_discard_attempt = {
                    "tile_key": context.get("tile_key"),
                    "tile_label": context.get("tile_label"),
                    "at": self.auto_last_click_at,
                    "frames": 0,
                }
            self.statusBar().showMessage("Auto: clique executado.")
            self.log_debug("Auto: clique executado apos atraso aleatorio.")
        finally:
            if not restarted and self.auto_click_pending:
                self.auto_click_pending = False
                self.auto_pending_context = None

    def pending_click_still_valid(self) -> bool:
        context = self.auto_pending_context or {}
        kind = context.get("kind")
        if kind == "discard":
            state = self.latest_auto_state
            if state is None:
                return False
            current_tile = self.next_auto_discard_candidate(state)
            if current_tile is None:
                return False
            current_key = current_tile.base_key
            if current_key != context.get("tile_key"):
                return False
            player_region = self.configured_region_rect("player_hand")
            for detection in self.latest_auto_detections:
                tile = tile_from_name(detection.name)
                if tile is None or tile.base_key != current_key:
                    continue
                if not player_region.contains(QPointF(detection.center_x, detection.center_y)):
                    continue
                if self.detection_crosses_player_closed_line(detection.y1, detection.y2):
                    return True
            return False
        if kind == "action":
            action = context.get("action")
            state = self.latest_auto_state
            if state is None or not action:
                return False
            visible_actions = {
                "Chii": state.chii_button_visible,
                "Pon": state.pon_button_visible,
                "Kan": state.kan_button_visible,
                "Riichi": self.latest_auto_riichi_button_visible,
                "Ron/Tsumo": self.latest_auto_win_button_visible,
            }
            recommended_actions = {
                decision.action
                for decision in state.call_decisions
                if decision.recommended
            }
            return bool(visible_actions.get(action) and action in recommended_actions)
        return True

    def restart_auto_from_latest_context(self) -> None:
        old_context = self.auto_pending_context or {}
        self.auto_click_pending = False
        self.auto_pending_context = None

        if not self.auto_play_checkbox.isChecked() or self.latest_auto_state is None:
            return

        if old_context.get("kind") == "discard":
            if self.latest_auto_image is None or not self.detect_own_turn(self.latest_auto_image):
                return
            if self.click_best_discard(self.latest_auto_detections, self.latest_auto_state):
                self.auto_last_click_at = time.perf_counter()
                self.auto_turn_frames = 0
                self.log_debug("Auto: nova pior peca agendada apos revalidacao.")

    def detect_player_winds(self, image: QImage) -> dict[str, str]:
        east_player = self.detect_east_player_by_marker(image)
        if east_player is not None:
            return self.infer_player_winds_from_east(east_player)
        return {"principal": "?", "esquerda": "?", "cima": "?", "direita": "?"}

    def detect_east_player_by_marker(self, image: QImage) -> str | None:
        probes = {
            "principal": "east_player",
            "esquerda": "east_left",
            "cima": "east_top",
            "direita": "east_right",
        }
        for player, key in probes.items():
            if self.probe_matches(image, key):
                return player
        return None

    @staticmethod
    def infer_player_winds_from_east(east_player: str) -> dict[str, str]:
        if east_player not in VISUAL_TURN_ORDER:
            return {"principal": "?", "esquerda": "?", "cima": "?", "direita": "?"}

        start = VISUAL_TURN_ORDER.index(east_player)
        return {
            VISUAL_TURN_ORDER[(start + offset) % 4]: WIND_ORDER[offset]
            for offset in range(4)
        }

    def detect_wind_letter(self, image: QImage, rect: QRectF) -> str | None:
        mask = self.wind_letter_mask(image, rect)
        if mask is None:
            return None

        template_scores = {
            letter: self.mask_similarity(mask, self.wind_template_mask(letter))
            for letter in WIND_LETTERS
        }
        letter, score = max(template_scores.items(), key=lambda item: item[1])
        self.log_debug(f"Vento local template: {letter} score={score:.2f}")
        return WIND_LETTERS[letter] if score >= 0.22 else None

    def wind_letter_mask(self, image: QImage, rect: QRectF, size: int = 42) -> list[list[bool]] | None:
        left = max(0, int(rect.left()))
        right = min(image.width(), int(rect.right()))
        top = max(0, int(rect.top()))
        bottom = min(image.height(), int(rect.bottom()))
        points: list[tuple[int, int]] = []
        for y in range(top, bottom):
            for x in range(left, right):
                color = image.pixelColor(x, y)
                if self.is_wind_letter_pixel(color):
                    points.append((x, y))

        if len(points) < 12:
            return None

        return self.normalized_mask(points, size)

    @staticmethod
    def normalized_mask(points: list[tuple[int, int]], size: int = 42) -> list[list[bool]] | None:
        if not points:
            return None
        min_x = min(x for x, _y in points)
        max_x = max(x for x, _y in points)
        min_y = min(y for _x, y in points)
        max_y = max(y for _x, y in points)
        width = max(1, max_x - min_x + 1)
        height = max(1, max_y - min_y + 1)
        mask = [[False for _ in range(size)] for _ in range(size)]
        scale = (size - 1) / max(width, height)
        offset_x = (size - 1 - width * scale) / 2
        offset_y = (size - 1 - height * scale) / 2
        for x, y in points:
            nx = min(size - 1, max(0, round(offset_x + (x - min_x) * scale)))
            ny = min(size - 1, max(0, round(offset_y + (y - min_y) * scale)))
            mask[ny][nx] = True
        return mask

    @staticmethod
    def is_wind_letter_pixel(color: QColor) -> bool:
        hue = color.hsvHue()
        is_cyan_text = 170 <= hue <= 205 and color.saturation() >= 55 and color.value() >= 115
        is_light_text = color.value() >= 145 and color.saturation() <= 120
        return is_cyan_text or is_light_text

    def wind_template_mask(self, letter: str, size: int = 42) -> list[list[bool]]:
        render_size = 84
        image = QImage(render_size, render_size, QImage.Format.Format_RGB32)
        image.fill(QColor("#000000"))
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QColor("#FFFFFF"))
        painter.setFont(QFont("Segoe UI", 58, QFont.Weight.Black))
        painter.drawText(image.rect(), Qt.AlignmentFlag.AlignCenter, letter)
        painter.end()
        points = [
            (x, y)
            for y in range(render_size)
            for x in range(render_size)
            if image.pixelColor(x, y).value() > 20
        ]
        return self.normalized_mask(points, size) or [[False for _ in range(size)] for _ in range(size)]

    @staticmethod
    def mask_similarity(mask: list[list[bool]], template: list[list[bool]]) -> float:
        intersection = 0
        union = 0
        for row_a, row_b in zip(mask, template):
            for value_a, value_b in zip(row_a, row_b):
                if value_a and value_b:
                    intersection += 1
                if value_a or value_b:
                    union += 1
        return intersection / union if union else 0.0

    @staticmethod
    def is_call_arrow_yellow(color: QColor) -> bool:
        return (
            color.red() >= 185
            and color.green() >= 120
            and color.blue() <= 95
            and color.red() > color.blue() * 2.0
            and color.green() > color.blue() * 1.4
        )

    @staticmethod
    def is_east_marker_red(color: QColor) -> bool:
        hue = color.hsvHue()
        return (
            (hue <= 14 or hue >= 345)
            and color.saturation() >= 85
            and color.value() >= 90
            and color.red() >= 120
            and color.red() > color.green() * 1.35
            and color.red() > color.blue() * 1.25
        )

    def scan_color_ratio(self, image: QImage, rect: QRectF, predicate) -> float:
        left = max(0, int(rect.left()))
        right = min(image.width(), int(rect.right()))
        top = max(0, int(rect.top()))
        bottom = min(image.height(), int(rect.bottom()))
        hits = 0
        sampled = 0
        for y in range(top, bottom, 3):
            for x in range(left, right, 3):
                if predicate(image.pixelColor(x, y)):
                    hits += 1
                sampled += 1
        return hits / sampled if sampled else 0.0

    def configured_region_rect(self, key: str) -> QRectF:
        region = self.app_config["regions"][key]
        return QRectF(int(region["x"]), int(region["y"]), int(region["w"]), int(region["h"]))

    def pixel_probe(self, key: str) -> dict | None:
        probe = self.app_config.get("pixel_probes", {}).get(key)
        if not probe or not probe.get("enabled", True):
            return None
        return probe

    def probe_matches(self, image: QImage | None, key: str) -> bool:
        probe = self.pixel_probe(key)
        if image is None or image.isNull() or probe is None:
            return False
        x = int(probe.get("x", 0))
        y = int(probe.get("y", 0))
        if not (0 <= x < image.width() and 0 <= y < image.height()):
            return False
        current = image.pixelColor(x, y)
        expected = QColor(str(probe.get("color", "#000000")))
        tolerance = int(probe.get("tolerance", 45))
        return self.color_distance(current, expected) <= tolerance

    @staticmethod
    def color_distance(current: QColor, expected: QColor) -> int:
        return max(
            abs(current.red() - expected.red()),
            abs(current.green() - expected.green()),
            abs(current.blue() - expected.blue()),
        )

    def button_probe_keys(self, action: str) -> tuple[str, ...]:
        mapping = {
            "Chii": ("button_chii_1", "button_chii_2"),
            "Pon": ("button_pon_1", "button_pon_2"),
            "Kan": ("button_kan_1", "button_kan_2"),
            "Riichi": ("button_riichi_1", "button_riichi_2"),
            "Ron/Tsumo": ("button_ron_1", "button_ron_2", "button_tsumo_1", "button_tsumo_2"),
            "Skip": ("button_skip_1", "button_skip_2"),
        }
        return mapping.get(action, tuple())

    def active_button_probe(self, image: QImage | None, action: str) -> dict | None:
        for key in self.button_probe_keys(action):
            if self.probe_matches(image, key):
                return self.pixel_probe(key)
        return None

    def probe_rect(self, probe: dict | None, size: int = 58) -> QRectF:
        if probe is None:
            return QRectF()
        half = size / 2
        return QRectF(float(probe["x"]) - half, float(probe["y"]) - half, size, size)

    def player_closed_line_y(self) -> float | None:
        region = self.app_config["regions"].get("player_closed_line")
        if not region or not region.get("enabled", True):
            return None
        return int(region["y"]) + int(region["h"]) / 2

    def detection_crosses_player_closed_line(self, y1: float, y2: float) -> bool:
        line_y = self.player_closed_line_y()
        return line_y is None or y1 <= line_y <= y2

    def qimage_to_rgb_array(self, image: QImage):
        import numpy as np

        rgb = image.convertToFormat(QImage.Format.Format_RGB888)
        width = rgb.width()
        height = rgb.height()
        ptr = rgb.bits()
        ptr.setsize(height * rgb.bytesPerLine())
        array = np.frombuffer(ptr, dtype=np.uint8).reshape((height, rgb.bytesPerLine()))
        return array[:, : width * 3].reshape((height, width, 3)).copy()

    def draw_predictions(self, image: QImage, result, state: HandState | None = None) -> None:
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))

        names = result.names
        detections = []
        for box in result.boxes:
            x1, y1, x2, y2 = [float(value) for value in box.xyxy[0]]
            class_id = int(box.cls[0])
            confidence = float(box.conf[0])
            name = str(names.get(class_id, class_id))
            label = f"{name} {confidence:.2f}"
            detections.append((x1, y1, x2, y2, label, name))

        label_rects: list[QRectF] = []
        discard_counts = Counter(tile.base_key for tile in state.discard_candidates) if state is not None else Counter()
        primary_discard_key = state.discard_candidates[0].base_key if state is not None and state.discard_candidates else None
        primary_discard_used = False
        player_region = self.configured_region_rect("player_hand")
        for x1, y1, x2, y2, label, name in detections:
            tile = tile_from_name(name)
            box_center = QPointF((x1 + x2) / 2, (y1 + y2) / 2)
            is_player_tile = player_region.contains(box_center)
            is_closed_player_tile = is_player_tile and self.detection_crosses_player_closed_line(y1, y2)
            is_discard_candidate = bool(tile and is_closed_player_tile and discard_counts[tile.base_key] > 0)
            discard_color = QColor("#22C55E")
            if is_discard_candidate:
                discard_counts[tile.base_key] -= 1
                if tile.base_key == primary_discard_key and not primary_discard_used:
                    discard_color = QColor("#EF4444")
                    primary_discard_used = True
                else:
                    discard_color = QColor("#FBBF24")
                painter.setPen(Qt.PenStyle.NoPen)
                fill = QColor(discard_color)
                fill.setAlpha(78)
                painter.setBrush(fill)
                painter.drawRect(int(x1), int(y1), int(x2 - x1), int(y2 - y1))

            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(discard_color, 2))
            painter.drawRect(int(x1), int(y1), int(x2 - x1), int(y2 - y1))

            label_rect = self.prediction_label_rect(
                image.width(),
                image.height(),
                QRectF(x1, y1, x2 - x1, y2 - y1),
                label,
                label_rects,
            )
            label_rects.append(label_rect)

            box_center_x = (x1 + x2) / 2
            box_center_y = (y1 + y2) / 2
            label_anchor_x = label_rect.center().x()
            label_anchor_y = label_rect.center().y()

            painter.setPen(QPen(QColor("#FBBF24"), 1))
            painter.drawLine(
                int(label_anchor_x),
                int(label_anchor_y),
                int(box_center_x),
                int(box_center_y),
            )
            label_background = QColor("#020617")
            label_background.setAlpha(205)
            painter.setPen(QPen(QColor("#0F172A"), 1))
            painter.setBrush(label_background)
            painter.drawRoundedRect(label_rect, 4, 4)
            painter.setPen(QColor("#FFFFFF"))
            painter.drawText(
                label_rect.adjusted(6, 0, -6, 0),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                label,
            )

        painter.end()

    def draw_call_indicators(self, image: QImage, state: HandState, call_source_player: str | None) -> None:
        active_call_prompt = (
            state.chii_button_visible
            or state.pon_button_visible
            or state.kan_button_visible
            or state.riichi_button_visible
            or state.win_button_visible
        )
        if not active_call_prompt:
            return

        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setFont(QFont("Segoe UI", 13, QFont.Weight.Bold))
        decisions = {decision.action: decision for decision in state.call_decisions}

        if state.chii_button_visible:
            roi = self.probe_rect(self.active_button_probe(image, "Chii"))
            decision = decisions.get("Chii")
            self.draw_call_box(
                painter,
                roi,
                self.call_decision_color(decision, QColor("#22C55E")),
                self.call_decision_label("CHII", decision, state.chii_options[0].compact if state.chii_options else ""),
            )

        if state.pon_button_visible:
            roi = self.probe_rect(self.active_button_probe(image, "Pon"))
            decision = decisions.get("Pon")
            self.draw_call_box(
                painter,
                roi,
                self.call_decision_color(decision, QColor("#06B6D4")),
                self.call_decision_label("PON", decision, state.pon_options[0].compact if state.pon_options else ""),
            )

        if state.kan_button_visible:
            roi = self.probe_rect(self.active_button_probe(image, "Kan"))
            decision = decisions.get("Kan")
            self.draw_call_box(
                painter,
                roi,
                self.call_decision_color(decision, QColor("#F97316")),
                self.call_decision_label("KAN", decision, state.kan_options[0].compact if state.kan_options else ""),
            )

        if state.riichi_button_visible:
            roi = self.probe_rect(self.active_button_probe(image, "Riichi"))
            decision = decisions.get("Riichi")
            self.draw_call_box(
                painter,
                roi,
                self.call_decision_color(decision, QColor("#F97316")),
                self.call_decision_label("RIICHI", decision, "botao visivel"),
            )

        if state.win_button_visible:
            roi = self.probe_rect(self.active_button_probe(image, "Ron/Tsumo"))
            decision = decisions.get("Ron/Tsumo")
            self.draw_call_box(
                painter,
                roi,
                self.call_decision_color(decision, QColor("#E11D48")),
                self.call_decision_label("RON/TSUMO", decision, "botao visivel"),
            )

        if call_source_player:
            arrow_rect = self.call_source_rect(image, call_source_player)
            if arrow_rect is not None:
                self.draw_call_box(painter, arrow_rect, QColor("#FBBF24"), f"Origem: {call_source_player}")
        painter.end()

    @staticmethod
    def call_decision_color(decision: CallDecision | None, fallback: QColor) -> QColor:
        if decision is None:
            return fallback
        return QColor("#22C55E") if decision.recommended else QColor("#EF4444")

    @staticmethod
    def call_decision_label(prefix: str, decision: CallDecision | None, fallback_option: str) -> str:
        if decision is None:
            return f"{prefix}: {fallback_option}" if fallback_option else prefix
        verdict = "SIM" if decision.recommended else "NAO"
        return f"{prefix} {verdict} {decision.confidence}%"

    def draw_call_box(self, painter: QPainter, rect: QRectF, color: QColor, label: str) -> None:
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(color, 3))
        painter.drawRoundedRect(rect, 8, 8)
        text_rect = QRectF(rect.left(), max(0, rect.top() - 30), min(640, CAPTURE_WIDTH - rect.left()), 26)
        painter.setPen(color.lighter(130))
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, label)

    def draw_detection_regions(self, image: QImage) -> None:
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        for key, region in self.app_config["regions"].items():
            if not region.get("enabled", True):
                continue
            rect = self.configured_region_rect(key)
            color = QColor(str(region.get("color", "#E5E7EB")))
            fill = QColor(color)
            fill.setAlpha(28)
            painter.setBrush(fill)
            painter.setPen(QPen(color, 2, Qt.PenStyle.DashLine))
            painter.drawRect(rect)
            if key == "player_closed_line":
                line_y = rect.center().y()
                painter.setPen(QPen(color, 4))
                painter.drawLine(int(rect.left()), int(line_y), int(rect.right()), int(line_y))

            label = str(region.get("label", key))
            label_rect = QRectF(rect.left() + 4, rect.top() + 4, max(90, len(label) * 8 + 12), 22)
            background = QColor("#020617")
            background.setAlpha(170)
            painter.fillRect(label_rect, background)
            painter.setPen(color.lighter(135))
            painter.drawText(label_rect.adjusted(5, 0, -4, 0), Qt.AlignmentFlag.AlignVCenter, label)
        for key, probe in self.app_config.get("pixel_probes", {}).items():
            if not probe.get("enabled", True):
                continue
            x = int(probe.get("x", 0))
            y = int(probe.get("y", 0))
            color = QColor(str(probe.get("color", "#FBBF24")))
            painter.setBrush(color)
            painter.setPen(QPen(QColor("#020617"), 2))
            painter.drawEllipse(QPointF(x, y), 6, 6)
            painter.setPen(QPen(color, 2))
            painter.drawLine(x - 14, y, x + 14, y)
            painter.drawLine(x, y - 14, x, y + 14)
            label = str(probe.get("label", key))
            label_rect = QRectF(x + 8, max(0, y - 25), max(80, len(label) * 7 + 10), 20)
            background = QColor("#020617")
            background.setAlpha(175)
            painter.fillRect(label_rect, background)
            painter.setPen(color.lighter(150))
            painter.drawText(label_rect.adjusted(4, 0, -4, 0), Qt.AlignmentFlag.AlignVCenter, label)
        painter.end()

    def call_source_rect(self, image: QImage, player: str) -> QRectF | None:
        probes = {
            "esquerda": self.pixel_probe("turn_left"),
            "direita": self.pixel_probe("turn_right"),
            "cima": self.pixel_probe("turn_top"),
            "principal": self.pixel_probe("turn_player"),
        }
        probe = probes.get(player)
        return self.probe_rect(probe) if probe else None

    def prediction_label_rect(
        self,
        image_width: int,
        image_height: int,
        box_rect: QRectF,
        label: str,
        used_rects: list[QRectF],
    ) -> QRectF:
        label_width = max(112, len(label) * 8 + 14)
        label_height = 24
        gap = 8
        step = label_height + 4

        candidates = []
        for offset in range(0, 10):
            shift = offset * step
            candidates.append(QRectF(box_rect.left(), box_rect.top() - gap - label_height - shift, label_width, label_height))

        for offset in range(0, 5):
            shift = offset * step
            candidates.append(QRectF(box_rect.left(), box_rect.bottom() + gap + shift, label_width, label_height))

        for offset in range(0, 5):
            shift = offset * step
            candidates.append(QRectF(box_rect.right() + gap + shift, box_rect.top(), label_width, label_height))
            candidates.append(QRectF(box_rect.left() - gap - label_width - shift, box_rect.top(), label_width, label_height))

        image_rect = QRectF(0, 0, image_width, image_height)
        for candidate in candidates:
            candidate = self.clamp_rect(candidate, image_rect)
            if not any(candidate.intersects(used.adjusted(-4, -4, 4, 4)) for used in used_rects):
                return candidate

        return self.clamp_rect(candidates[0], image_rect)

    def clamp_rect(self, rect: QRectF, bounds: QRectF) -> QRectF:
        x = min(max(rect.left(), bounds.left()), bounds.right() - rect.width())
        y = min(max(rect.top(), bounds.top()), bounds.bottom() - rect.height())
        return QRectF(x, y, rect.width(), rect.height())

    def save_screenshot(self) -> None:
        self.capture_once(force=True)

        if self.last_capture is None:
            self.statusBar().showMessage("Nenhum frame disponivel para salvar.")
            return

        RAW_DATASET_DIR.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        path = RAW_DATASET_DIR / f"mahjong_soul_{timestamp}.png"

        if self.last_capture.save(str(path), "PNG"):
            self.statusBar().showMessage(f"Screenshot salvo em {path}")
        else:
            self.statusBar().showMessage("Falha ao salvar screenshot.")

    def open_annotator(self) -> None:
        if self.annotator_window is None:
            self.annotator_window = AnnotatorWindow()

        self.annotator_window.show()
        self.annotator_window.raise_()
        self.annotator_window.activateWindow()

    def open_training(self) -> None:
        if self.training_window is None:
            from mahjong_master.training_window import TrainingWindow

            self.training_window = TrainingWindow()

        self.training_window.show()
        self.training_window.raise_()
        self.training_window.activateWindow()

    def open_region_config(self) -> None:
        if self.region_config_dialog is None:
            self.region_config_dialog = RegionConfigDialog(self.app_config, self)

        self.region_config_dialog.set_preview_image(self.last_capture)
        self.region_config_dialog.show()
        self.region_config_dialog.raise_()
        self.region_config_dialog.activateWindow()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self.capture_once()

    def closeEvent(self, event) -> None:  # noqa: N802
        self.screen_capture.close()
        super().closeEvent(event)


def main() -> int:
    _enable_dpi_awareness()
    app = QApplication(sys.argv)
    apply_dark_theme(app)
    window = MainWindow()
    hotkey_filter = GlobalHotkeyFilter(
        HOTKEY_ID_SAVE_SCREENSHOT,
        VK_F3,
        window.save_screenshot,
    )
    auto_hotkey_filter = GlobalHotkeyFilter(
        HOTKEY_ID_AUTO_PLAY,
        VK_TAB,
        window.toggle_auto_play,
    )
    app.installNativeEventFilter(hotkey_filter)
    app.installNativeEventFilter(auto_hotkey_filter)
    app.aboutToQuit.connect(hotkey_filter.unregister)
    app.aboutToQuit.connect(auto_hotkey_filter.unregister)

    if hotkey_filter.registered and auto_hotkey_filter.registered:
        window.statusBar().showMessage("F3 salva screenshot; Tab liga/desliga AutoPlay.")
    elif hotkey_filter.registered:
        window.statusBar().showMessage("F3 salva screenshot. Tab global indisponivel; funciona com foco no app.")
    elif auto_hotkey_filter.registered:
        window.statusBar().showMessage("Tab liga/desliga AutoPlay. F3 global indisponivel.")
    else:
        window.statusBar().showMessage("Nao foi possivel registrar F3/Tab como atalhos globais.")

    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
