from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time
from collections import Counter, deque
from dataclasses import dataclass
from datetime import datetime
from html import escape
from pathlib import Path

import mss
from PyQt6.QtCore import QObject, QPointF, QAbstractNativeEventFilter, QRectF, Qt, QThread, QTimer, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QColor, QFont, QIcon, QImage, QKeySequence, QPainter, QPen, QPixmap, QShortcut, QTransform
from PyQt6.QtWidgets import (
    QApplication,
    QButtonGroup,
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
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QStatusBar,
    QTextBrowser,
    QToolButton,
    QVBoxLayout,
    QWidgetAction,
    QWidget,
)

from mahjong_master.annotator import AnnotatorWindow
from mahjong_master.game_analyzer import GameAnalyzer, detections_from_yolo_result
from mahjong_master.mahjong_logic import (
    BOT_LOGIC_NOTES,
    BOT_LOGIC_VERSION,
    CallDecision,
    HandState,
    Tile,
    call_candidate_plans,
    call_decisions_for_state,
    call_plan_score_from_plans,
    estimated_hand_value,
    hand_completion_progress,
    hand_shape_profile,
    likely_yaku,
    riichi_discard_candidates,
    simulate_call,
    table_risk_pressure,
    tile_from_name,
)
from mahjong_master.screen_regions import load_config, reset_config, save_config
from mahjong_master.theme import apply_dark_theme


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RAW_DATASET_DIR = PROJECT_ROOT / "dataset" / "raw"
LOGS_DIR = PROJECT_ROOT / "logs"
AUTO_LOG_DIR = LOGS_DIR / "autoplay"
RUNS_DIR = PROJECT_ROOT / "runs" / "detect"
CONFIG_PATH = PROJECT_ROOT / "configs.json"
ASSETS_DIR = PROJECT_ROOT / "assets"
DEFAULT_WINDOW_TITLE = "MahjongSoul-Steam"
HOTKEY_ID_SAVE_SCREENSHOT = 1
HOTKEY_ID_AUTO_PLAY = 2
HOTKEY_ID_PREVIEW = 4
HOTKEY_ID_CAPTURE = 5
HOTKEY_ID_ROUTINES = 6
HOTKEY_ID_AREAS_OVERLAY = 7
HOTKEY_ID_OPTIONS = 8
HOTKEY_ID_ANNOTATOR = 9
HOTKEY_ID_TRAINING = 10
HOTKEY_ID_REGION_CONFIG = 11
HOTKEY_ID_TERMINAL = 12
VK_F3 = 0x72
VK_TAB = 0x09
VK_A = 0x41
VK_C = 0x43
VK_E = 0x45
VK_M = 0x4D
VK_N = 0x4E
VK_O = 0x4F
VK_R = 0x52
VK_T = 0x54
VK_V = 0x56
WM_HOTKEY = 0x0312
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
CAPTURE_WIDTH = 1592
CAPTURE_HEIGHT = 933
PREDICT_IMAGE_SIZE = 1600
SCORE_OCR_INTERVAL_SECONDS = 5.0
WIND_ORDER = ("east", "south", "west", "north")
WIND_LETTERS = {"E": "east", "S": "south", "W": "west", "N": "north"}
VISUAL_TURN_ORDER = ("esquerda", "principal", "direita", "cima")
AUTO_LOG_DICTIONARY = {
    "TS": "hora local HH:MM:SS",
    "EV": "tipo do evento: FRAME, AUTO_CLICK, ROUTINE_CLICK, SESSION_END",
    "MP": "mao do jogador detectada",
    "MA": "melds/chamadas abertas do jogador",
    "PC": "peca comprada inferida pela diferenca contra o frame anterior",
    "DP": "descartes do jogador principal",
    "DE": "descartes do jogador da esquerda",
    "DC": "descartes do jogador de cima",
    "DD": "descartes do jogador da direita",
    "AE": "pecas abertas do jogador da esquerda",
    "AC": "pecas abertas do jogador de cima",
    "AD": "pecas abertas do jogador da direita",
    "SC": "score OCR: P jogador, E esquerda, C cima, D direita",
    "VE": "ventos: P/E/C/D",
    "DR": "doras reais",
    "DI": "indicadores de dora",
    "ACT": "botoes/chamadas visiveis",
    "RI": "jogadores com riichi detectado",
    "SH": "shanten",
    "PG": "progresso percentual ate tenpai/mao pronta",
    "ST": "modo de estrategia",
    "RK": "pressao/risco da mesa",
    "OBJ": "objetivo de ataque atual",
    "DS": "descartes recomendados",
    "FW": "furiten atual",
    "FA": "furiten apos descartes candidatos",
    "DT": "deteccoes YOLO compactas",
}

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
    def __init__(self, hotkey_id: int, virtual_key: int, callback, auto_register: bool = True) -> None:
        super().__init__()
        self.hotkey_id = hotkey_id
        self.virtual_key = virtual_key
        self.callback = callback
        self.registered = False
        if auto_register:
            self.register()

    def nativeEventFilter(self, event_type, message):  # noqa: N802
        if event_type not in (b"windows_generic_MSG", b"windows_dispatcher_MSG"):
            return False, 0

        msg = Msg.from_address(int(message))
        if msg.message == WM_HOTKEY and msg.wParam == self.hotkey_id:
            return bool(self.callback()), 0

        return False, 0

    def register(self) -> None:
        if not self.registered:
            self.registered = bool(user32.RegisterHotKey(None, self.hotkey_id, 0, self.virtual_key))

    def unregister(self) -> None:
        if self.registered:
            user32.UnregisterHotKey(None, self.hotkey_id)
            self.registered = False

    def set_registered(self, enabled: bool) -> None:
        if enabled:
            self.register()
        else:
            self.unregister()


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


@dataclass(frozen=True)
class ChiiOptionCluster:
    tiles: tuple[Tile, Tile]
    center_x: float
    center_y: float
    signature: tuple[str, str]
    confidence: float


class PredictionWorker(QObject):
    finished = pyqtSignal(int, object, object, object, float)
    failed = pyqtSignal(int, str)
    model_loaded = pyqtSignal(str)

    def __init__(self) -> None:
        super().__init__()
        self.model = None
        self.model_path: str | None = None
        self.configure_cpu_threads()

    @staticmethod
    def configure_cpu_threads() -> None:
        try:
            import torch

            worker_count = max(1, (os.cpu_count() or 2) - 1)
            torch.set_num_threads(worker_count)
            torch.set_num_interop_threads(max(1, min(4, worker_count)))
        except Exception:
            return

    @pyqtSlot(int, object, dict)
    def predict(self, request_id: int, frame_array, context: dict) -> None:
        try:
            model = self.load_model(str(context["model_path"]))
            started_at = time.perf_counter()
            result = model.predict(
                source=frame_array,
                imgsz=int(context["imgsz"]),
                conf=float(context["conf"]),
                device=str(context["device"]),
                verbose=False,
            )[0]
            detections = detections_from_yolo_result(result)
            state = GameAnalyzer(
                int(context["capture_width"]),
                int(context["capture_height"]),
                context["regions"],
            ).analyze(
                detections,
                chii_button_visible=bool(context["chii_button_visible"]),
                pon_button_visible=bool(context["pon_button_visible"]),
                kan_button_visible=bool(context["kan_button_visible"]),
                call_source_player=context.get("call_source_player"),
                player_winds=context.get("player_winds"),
            )
            elapsed_ms = (time.perf_counter() - started_at) * 1000
            self.finished.emit(request_id, result, detections, state, elapsed_ms)
        except Exception as error:
            self.failed.emit(request_id, f"{type(error).__name__}: {error}")

    @pyqtSlot()
    def unload(self) -> None:
        self.model = None
        self.model_path = None

    def load_model(self, model_path: str):
        if self.model is not None and self.model_path == model_path:
            return self.model

        from ultralytics import RTDETR, YOLO

        is_rtdetr = "rtdetr" in Path(model_path).name.lower()
        self.model = RTDETR(model_path) if is_rtdetr else YOLO(model_path)
        self.model_path = model_path
        class_count = len(getattr(self.model, "names", {}) or {})
        self.model_loaded.emit(f"Modelo carregado no worker: {Path(model_path).name} | {class_count} classes")
        return self.model


class TerminalWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("MahjongMaster - Terminal")
        self.resize(900, 620)
        self.browser = QTextBrowser()
        self.browser.setOpenExternalLinks(False)
        self.browser.setPlaceholderText("Resumo da mao aparece aqui apos o predict.")
        self.browser.setStyleSheet(
            "QTextBrowser { background: #0B1220; border: 1px solid #1F2937; border-radius: 4px; }"
        )
        self.setCentralWidget(self.browser)

    def closeEvent(self, event) -> None:  # noqa: N802
        event.ignore()
        self.hide()


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

    def set_interactive(self, enabled: bool) -> None:
        flags = self.flags()
        if enabled:
            flags |= (
                QGraphicsItem.GraphicsItemFlag.ItemIsMovable
                | QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
            )
            self.setAcceptedMouseButtons(Qt.MouseButton.LeftButton)
        else:
            flags &= ~QGraphicsItem.GraphicsItemFlag.ItemIsMovable
            flags &= ~QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
            self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
            self.setSelected(False)
        self.setFlags(flags)

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

    def set_interactive(self, enabled: bool) -> None:
        flags = self.flags()
        if enabled:
            flags |= (
                QGraphicsItem.GraphicsItemFlag.ItemIsMovable
                | QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
            )
            self.setAcceptedMouseButtons(Qt.MouseButton.LeftButton)
        else:
            flags &= ~QGraphicsItem.GraphicsItemFlag.ItemIsMovable
            flags &= ~QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
            self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
            self.setSelected(False)
        self.setFlags(flags)

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
        self.edit_mode = "region"
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
        self.set_edit_mode(self.edit_mode)
        self.fitInView(self.scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    def set_edit_mode(self, mode: str) -> None:
        self.edit_mode = mode if mode == "probe" else "region"
        for item in self.region_items.values():
            item.set_interactive(self.edit_mode == "region")
        for item in self.probe_items.values():
            item.set_interactive(self.edit_mode == "probe")
        if self.edit_mode == "region":
            self.selected_probe_key = None

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
        if self.edit_mode == "probe":
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
        if self.edit_mode == "probe" and self.selected_probe_key and event.button() == Qt.MouseButton.LeftButton:
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
        self.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, True)
        self.setSizeGripEnabled(True)
        self.resize(980, 640)
        self.setMinimumSize(560, 380)
        self.config = config
        self.current_key: str | None = None
        self.current_probe_key: str | None = None
        self.edit_mode = "region"

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
        self.region_combo.setMinimumContentsLength(18)

        self.enabled_checkbox = QCheckBox("Ativa")
        self.x_input = self.region_spinbox(CAPTURE_WIDTH)
        self.y_input = self.region_spinbox(CAPTURE_HEIGHT)
        self.w_input = self.region_spinbox(CAPTURE_WIDTH)
        self.h_input = self.region_spinbox(CAPTURE_HEIGHT)

        self.probe_combo = QComboBox()
        for key, probe in self.config.get("pixel_probes", {}).items():
            self.probe_combo.addItem(f"{probe['label']} ({key})", key)
        self.probe_combo.currentIndexChanged.connect(self.load_selected_probe)
        self.probe_combo.setMinimumContentsLength(18)
        self.probe_combo.setMaxVisibleItems(12)
        self.probe_combo.view().setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.probe_enabled_checkbox = QCheckBox("Ativo")
        self.probe_x_input = self.region_spinbox(CAPTURE_WIDTH)
        self.probe_y_input = self.region_spinbox(CAPTURE_HEIGHT)
        self.probe_tolerance_input = self.region_spinbox(255)
        self.probe_color_label = QLabel("-")
        self.probe_color_label.setMinimumWidth(78)
        self.probe_mode_combo = QComboBox()
        self.probe_mode_combo.addItem("Cor igual", "match")
        self.probe_mode_combo.addItem("Cor diferente", "not_match")
        self.probe_mode_combo.setFixedWidth(118)

        self.area_mode_button = QPushButton("Editar area")
        self.area_mode_button.setCheckable(True)
        self.area_mode_button.setChecked(True)
        self.pixel_mode_button = QPushButton("Editar pixel")
        self.pixel_mode_button.setCheckable(True)
        self.mode_group = QButtonGroup(self)
        self.mode_group.setExclusive(True)
        self.mode_group.addButton(self.area_mode_button)
        self.mode_group.addButton(self.pixel_mode_button)
        self.area_mode_button.clicked.connect(lambda: self.set_edit_mode("region"))
        self.pixel_mode_button.clicked.connect(lambda: self.set_edit_mode("probe"))

        save_button = QPushButton("Salvar area")
        save_button.clicked.connect(self.save_current_region)
        save_probe_button = QPushButton("Salvar pixel")
        save_probe_button.clicked.connect(self.save_current_probe)
        refresh_screen_button = QPushButton("Atualizar tela")
        refresh_screen_button.clicked.connect(self.refresh_frozen_screen)
        reset_button = QPushButton("Restaurar padrao")
        reset_button.clicked.connect(self.reset_to_defaults)

        button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        button_box.rejected.connect(self.reject)

        layout = QVBoxLayout()
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)
        layout.addWidget(self.editor_view, stretch=1)
        controls_grid = QGridLayout()
        controls_grid.setHorizontalSpacing(8)
        controls_grid.setVerticalSpacing(4)
        controls_grid.addWidget(QLabel("Area"), 0, 0)
        controls_grid.addWidget(self.region_combo, 0, 1, 1, 3)
        controls_grid.addWidget(self.enabled_checkbox, 0, 4)
        controls_grid.addWidget(QLabel("X"), 0, 5)
        controls_grid.addWidget(self.x_input, 0, 6)
        controls_grid.addWidget(QLabel("Y"), 0, 7)
        controls_grid.addWidget(self.y_input, 0, 8)
        controls_grid.addWidget(QLabel("L"), 0, 9)
        controls_grid.addWidget(self.w_input, 0, 10)
        controls_grid.addWidget(QLabel("A"), 0, 11)
        controls_grid.addWidget(self.h_input, 0, 12)
        controls_grid.addWidget(QLabel("Pixel"), 1, 0)
        controls_grid.addWidget(self.probe_combo, 1, 1, 1, 3)
        controls_grid.addWidget(self.probe_enabled_checkbox, 1, 4)
        controls_grid.addWidget(QLabel("X"), 1, 5)
        controls_grid.addWidget(self.probe_x_input, 1, 6)
        controls_grid.addWidget(QLabel("Y"), 1, 7)
        controls_grid.addWidget(self.probe_y_input, 1, 8)
        controls_grid.addWidget(QLabel("Cor"), 1, 9)
        controls_grid.addWidget(self.probe_color_label, 1, 10)
        controls_grid.addWidget(QLabel("Tol"), 1, 11)
        controls_grid.addWidget(self.probe_tolerance_input, 1, 12)
        controls_grid.addWidget(self.probe_mode_combo, 1, 13)
        controls_grid.setColumnStretch(1, 1)
        controls_grid.setColumnStretch(2, 1)
        controls_grid.setColumnStretch(3, 1)
        layout.addLayout(controls_grid)
        actions = QHBoxLayout()
        actions.addWidget(self.area_mode_button)
        actions.addWidget(self.pixel_mode_button)
        actions.addSpacing(12)
        actions.addWidget(save_button)
        actions.addWidget(save_probe_button)
        actions.addWidget(refresh_screen_button)
        actions.addWidget(reset_button)
        actions.addStretch(1)
        layout.addLayout(actions)
        layout.addWidget(button_box)
        self.setLayout(layout)
        self.load_selected_region()
        self.load_selected_probe()
        self.set_edit_mode("region")

    def set_preview_image(self, image: QImage | None) -> None:
        self.editor_view.set_image(image)
        if self.edit_mode == "region" and self.current_key:
            self.editor_view.select_key(self.current_key)
        if self.edit_mode == "probe" and self.current_probe_key:
            self.editor_view.select_probe_key(self.current_probe_key)

    def refresh_frozen_screen(self) -> None:
        parent = self.parent()
        if parent and hasattr(parent, "capture_once"):
            parent.capture_once(force=True)
        image = getattr(parent, "last_capture", None) if parent is not None else None
        self.set_preview_image(image)

    def set_edit_mode(self, mode: str) -> None:
        self.edit_mode = mode if mode == "probe" else "region"
        self.area_mode_button.setChecked(self.edit_mode == "region")
        self.pixel_mode_button.setChecked(self.edit_mode == "probe")
        self.editor_view.set_edit_mode(self.edit_mode)
        if self.edit_mode == "region" and self.current_key:
            self.editor_view.select_key(self.current_key)
        elif self.edit_mode == "probe" and self.current_probe_key:
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
        if self.edit_mode == "region":
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
        mode_index = self.probe_mode_combo.findData(str(probe.get("mode", "match")))
        self.probe_mode_combo.setCurrentIndex(max(0, mode_index))
        self.set_probe_color_label(str(probe.get("color", "#FBBF24")))
        if self.edit_mode == "probe":
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
        probe["mode"] = self.probe_mode_combo.currentData() or "match"
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
        self.probe_combo.setMaxVisibleItems(12)
        self.probe_combo.blockSignals(False)
        self.editor_view.reload_regions()
        self.load_selected_region()
        self.load_selected_probe()
        if self.parent() and hasattr(self.parent(), "capture_once"):
            self.parent().capture_once(force=True)


class RoutineConfigDialog(QDialog):
    def __init__(self, config: dict, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Rotinas simples")
        self.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, True)
        self.setSizeGripEnabled(True)
        self.resize(980, 640)
        self.setMinimumSize(760, 480)
        self.config = config
        self.current_rule_key: str | None = None
        self.editor_config = {"regions": {}, "pixel_probes": self.config.setdefault("routine_rules", {})}

        self.editor_view = RegionEditorView(
            self.editor_config,
            lambda *_args: None,
            self.rule_changed_from_editor,
        )
        self.editor_view.set_edit_mode("probe")
        if parent is not None and getattr(parent, "last_capture", None) is not None:
            self.editor_view.set_image(parent.last_capture)
        else:
            self.editor_view.set_image(None)

        help_label = QLabel(
            "Cada regra faz uma coisa: se o pixel estiver com a cor esperada, espera o delay e clica nesse mesmo pixel. "
            "Tolerancia e a diferenca maxima por canal RGB: 0 exige cor exata; 30-70 aceita variacao de brilho."
        )
        help_label.setWordWrap(True)
        help_label.setStyleSheet("QLabel { color: #CBD5E1; font-size: 13px; padding: 6px; }")

        self.rule_combo = QComboBox()
        self.rule_combo.setMinimumHeight(34)
        self.rule_combo.currentIndexChanged.connect(self.load_selected_rule)
        self.enabled_checkbox = QCheckBox("Ativa")
        self.label_input = QLineEdit()
        self.x_input = self.spinbox(CAPTURE_WIDTH - 1)
        self.y_input = self.spinbox(CAPTURE_HEIGHT - 1)
        self.tolerance_input = self.spinbox(255)
        self.delay_input = QDoubleSpinBox()
        self.delay_input.setRange(0.0, 30.0)
        self.delay_input.setSingleStep(0.1)
        self.delay_input.setDecimals(1)
        self.delay_input.setSuffix(" s")
        self.color_label = QLabel("-")
        self.color_label.setMinimumWidth(110)
        self.color_label.setMinimumHeight(28)

        form = QGridLayout()
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(10)
        form.addWidget(QLabel("Regra"), 0, 0)
        form.addWidget(self.rule_combo, 0, 1, 1, 3)
        form.addWidget(self.enabled_checkbox, 0, 4)
        form.addWidget(QLabel("Nome"), 1, 0)
        form.addWidget(self.label_input, 1, 1, 1, 4)
        form.addWidget(QLabel("X"), 2, 0)
        form.addWidget(self.x_input, 2, 1)
        form.addWidget(QLabel("Y"), 2, 2)
        form.addWidget(self.y_input, 2, 3)
        form.addWidget(QLabel("Cor"), 3, 0)
        form.addWidget(self.color_label, 3, 1)
        form.addWidget(QLabel("Tolerancia"), 3, 2)
        form.addWidget(self.tolerance_input, 3, 3)
        form.addWidget(QLabel("Delay do clique"), 4, 0)
        form.addWidget(self.delay_input, 4, 1)
        form.setColumnStretch(1, 1)
        form.setColumnStretch(3, 1)

        self.add_button = QPushButton("Adicionar condicao + acao")
        self.save_button = QPushButton("Salvar regra")
        self.delete_button = QPushButton("Remover regra")
        self.refresh_button = QPushButton("Atualizar imagem")
        for button in (self.add_button, self.save_button, self.delete_button, self.refresh_button):
            button.setMinimumHeight(42)
            button.setMinimumWidth(150)
        self.add_button.clicked.connect(self.add_rule)
        self.save_button.clicked.connect(self.save_current_rule)
        self.delete_button.clicked.connect(self.delete_rule)
        self.refresh_button.clicked.connect(self.refresh_frozen_screen)

        actions = QGridLayout()
        actions.setHorizontalSpacing(8)
        actions.setVerticalSpacing(8)
        actions.addWidget(self.add_button, 0, 0)
        actions.addWidget(self.save_button, 0, 1)
        actions.addWidget(self.delete_button, 1, 0)
        actions.addWidget(self.refresh_button, 1, 1)

        rule_panel = QVBoxLayout()
        rule_panel.addWidget(help_label)
        rule_panel.addLayout(form)
        rule_panel.addSpacing(10)
        rule_panel.addLayout(actions)
        rule_panel.addStretch(1)

        body = QHBoxLayout()
        body.addWidget(self.editor_view, stretch=3)
        body.addLayout(rule_panel, stretch=2)

        button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        button_box.rejected.connect(self.reject)

        layout = QVBoxLayout()
        layout.addLayout(body, stretch=1)
        layout.addWidget(button_box)
        self.setLayout(layout)

        self.reload_rule_combo()
        self.load_selected_rule()

    def spinbox(self, maximum: int) -> QSpinBox:
        spinbox = QSpinBox()
        spinbox.setRange(0, maximum)
        spinbox.setSingleStep(5)
        return spinbox

    def set_preview_image(self, image: QImage | None) -> None:
        self.editor_view.set_image(image)
        self.editor_view.set_edit_mode("probe")
        if self.current_rule_key:
            self.editor_view.select_probe_key(self.current_rule_key)

    def refresh_frozen_screen(self) -> None:
        parent = self.parent()
        if parent and hasattr(parent, "capture_once"):
            parent.capture_once(force=True)
        self.set_preview_image(getattr(parent, "last_capture", None) if parent is not None else None)

    def reload_rule_combo(self) -> None:
        current = self.current_rule_key
        self.rule_combo.blockSignals(True)
        self.rule_combo.clear()
        for key, rule in self.config.setdefault("routine_rules", {}).items():
            self.rule_combo.addItem(f"{rule.get('label', key)} ({key})", key)
        index = self.rule_combo.findData(current) if current else 0
        self.rule_combo.setCurrentIndex(max(0, index))
        self.rule_combo.blockSignals(False)
        self.current_rule_key = self.rule_combo.currentData()
        self.editor_config["pixel_probes"] = self.config.setdefault("routine_rules", {})
        self.editor_view.config = self.editor_config
        self.editor_view.reload_regions()

    def load_selected_rule(self) -> None:
        self.current_rule_key = self.rule_combo.currentData()
        if not self.current_rule_key:
            return
        rule = self.config["routine_rules"][self.current_rule_key]
        self.enabled_checkbox.setChecked(bool(rule.get("enabled", True)))
        self.label_input.setText(str(rule.get("label", self.current_rule_key)))
        self.x_input.setValue(int(rule.get("x", 0)))
        self.y_input.setValue(int(rule.get("y", 0)))
        self.tolerance_input.setValue(int(rule.get("tolerance", 45)))
        self.delay_input.setValue(float(rule.get("delay_seconds", 1.0)))
        self.set_rule_color_label(str(rule.get("color", "#FBBF24")))
        self.editor_view.select_probe_key(self.current_rule_key)

    def save_current_rule(self) -> None:
        if not self.current_rule_key:
            return
        rule = self.config["routine_rules"][self.current_rule_key]
        rule["enabled"] = self.enabled_checkbox.isChecked()
        rule["label"] = self.label_input.text().strip() or self.current_rule_key
        rule["x"] = self.x_input.value()
        rule["y"] = self.y_input.value()
        rule["tolerance"] = self.tolerance_input.value()
        rule["delay_seconds"] = self.delay_input.value()
        save_config(CONFIG_PATH, self.config)
        self.reload_rule_combo()
        if self.parent() and hasattr(self.parent(), "reset_routine_runtime_state"):
            self.parent().reset_routine_runtime_state()
        if self.parent() and hasattr(self.parent(), "capture_once"):
            self.parent().capture_once(force=True)

    def add_rule(self) -> None:
        rules = self.config.setdefault("routine_rules", {})
        index = 1
        while f"routine_rule_{index}" in rules:
            index += 1
        key = f"routine_rule_{index}"
        rules[key] = {
            "label": f"Regra {index}",
            "x": CAPTURE_WIDTH // 2,
            "y": CAPTURE_HEIGHT // 2,
            "color": "#FBBF24",
            "tolerance": 45,
            "delay_seconds": 1.0,
            "enabled": True,
        }
        self.current_rule_key = key
        save_config(CONFIG_PATH, self.config)
        self.reload_rule_combo()
        self.load_selected_rule()

    def delete_rule(self) -> None:
        if not self.current_rule_key:
            return
        self.config.setdefault("routine_rules", {}).pop(self.current_rule_key, None)
        self.current_rule_key = None
        save_config(CONFIG_PATH, self.config)
        if self.parent() and hasattr(self.parent(), "reset_routine_runtime_state"):
            self.parent().reset_routine_runtime_state()
        self.reload_rule_combo()
        self.load_selected_rule()

    def rule_changed_from_editor(self, key: str, x: int, y: int, color: str, committed: bool) -> None:
        rule = self.config.setdefault("routine_rules", {}).get(key)
        if rule is None:
            return
        rule["x"] = x
        rule["y"] = y
        rule["color"] = color
        if key == self.current_rule_key:
            self.x_input.setValue(x)
            self.y_input.setValue(y)
            self.set_rule_color_label(color)
        if committed:
            save_config(CONFIG_PATH, self.config)
            if self.parent() and hasattr(self.parent(), "reset_routine_runtime_state"):
                self.parent().reset_routine_runtime_state()
            if self.parent() and hasattr(self.parent(), "capture_once"):
                self.parent().capture_once(force=True)

    def set_rule_color_label(self, color: str) -> None:
        self.color_label.setText(color)
        self.color_label.setStyleSheet(f"QLabel {{ background: {color}; color: #FFFFFF; padding: 5px; }}")

    @staticmethod
    def safe_int(value: str, minimum: int, maximum: int) -> int:
        try:
            number = int(float(value.replace(",", ".")))
        except (TypeError, ValueError):
            number = minimum
        return max(minimum, min(maximum, number))

    @staticmethod
    def safe_float(value: str, minimum: float, maximum: float) -> float:
        try:
            number = float(value.replace(",", "."))
        except (TypeError, ValueError):
            number = minimum
        return max(minimum, min(maximum, number))


class MainWindow(QMainWindow):
    prediction_requested = pyqtSignal(int, object, dict)
    prediction_unload_requested = pyqtSignal()
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
        self.predict_in_flight = False
        self.predict_request_id = 0
        self.pending_prediction_images: dict[int, QImage] = {}
        self.last_game_summary_html = ""
        self.pending_game_summary_html: str | None = None
        self.summary_scroll_active_until = 0.0
        self.app_config = load_config(CONFIG_PATH)
        save_config(CONFIG_PATH, self.app_config)
        self.capture_count = 0
        self.predict_count = 0
        self.last_game_summary = "Hand: - | Yaku provavel: -"
        self.auto_turn_frames = 0
        self.auto_decision_history: dict[str, deque[bool]] = {
            action: deque(maxlen=3)
            for action in ("Chii", "Pon", "Kan", "Riichi")
        }
        self.auto_last_click_at = 0.0
        self.auto_click_pending = False
        self.auto_pending_context: dict | None = None
        self.auto_click_generation = 0
        self.auto_blocked_discard_keys: dict[str, float] = {}
        self.auto_last_discard_attempt: dict | None = None
        self.auto_call_settle_until = 0.0
        self.auto_riichi_discard_until = 0.0
        self.chii_option_history: deque[list[ChiiOptionCluster]] = deque(maxlen=4)
        self.chii_option_header_frames = 0
        self.auto_decision_log_path: Path | None = None
        self.auto_decision_log_last_at = 0.0
        self.auto_decision_log_last_signature = ""
        self.auto_decision_log_events = 0
        self.auto_no_hand_frames = 0
        self.auto_log_previous_hand_counter = Counter()
        self.auto_log_previous_hand_counter: Counter[str] = Counter()
        self.routine_step_index: dict[str, int] = {}
        self.routine_step_repeat_count: dict[str, int] = {}
        self.routine_last_click_at: dict[str, float] = {}
        self.routine_step_ready_since: dict[str, float] = {}
        self.routine_click_pending = False
        self.latest_routine_statuses: list[dict] = []
        self.routine_status_log_last_at = 0.0
        self.latest_score_ocr: dict[str, dict] = {}
        self.score_ocr_last_at = 0.0
        self.score_ocr_error_logged = False
        self.score_ocr_reads_this_hand = 0
        self.score_ocr_no_hand_frames = 0
        self.capture_threshold_stage = 1
        self.capture_waiting_for_reset = False
        self.riichi_discard_marks: dict[str, int] = {}
        self.riichi_probe_hits: dict[str, int] = {}
        self.main_shortcuts: list[QShortcut] = []
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
        self.auto_call_settle_seconds = float(self.app_config.get("auto_call_settle_seconds", 1.6))
        self.annotator_window: AnnotatorWindow | None = None
        self.training_window = None
        self.region_config_dialog: RegionConfigDialog | None = None
        self.routine_config_dialog: RoutineConfigDialog | None = None
        self.prediction_thread = QThread(self)
        self.prediction_worker = PredictionWorker()
        self.prediction_worker.moveToThread(self.prediction_thread)
        self.prediction_requested.connect(self.prediction_worker.predict)
        self.prediction_unload_requested.connect(self.prediction_worker.unload)
        self.prediction_worker.finished.connect(self.prediction_finished)
        self.prediction_worker.failed.connect(self.prediction_failed)
        self.prediction_worker.model_loaded.connect(self.log_debug)
        self.prediction_thread.start()

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
        self.routine_config_button = QPushButton("Rotinas")
        self.routine_config_button.clicked.connect(self.open_routine_config)
        self.terminal_button = QPushButton("Terminal")
        self.terminal_button.clicked.connect(self.open_terminal)

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

        self.capture_mode_checkbox = QCheckBox("Capt")
        self.capture_mode_checkbox.setToolTip("Salva screenshot automaticamente a cada 3 minutos.")
        self.capture_mode_checkbox.setChecked(bool(self.app_config.get("capture_mode_enabled", False)))
        self.capture_mode_checkbox.stateChanged.connect(self.capture_mode_changed)

        self.routines_checkbox = QCheckBox("Rot")
        self.routines_checkbox.setToolTip("Executa rotinas por triggers de pixel configurados.")
        self.routines_checkbox.setChecked(bool(self.app_config.get("routines_enabled", False)))
        self.routines_checkbox.stateChanged.connect(self.routines_mode_changed)

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

        self.auto_call_settle_input = QDoubleSpinBox()
        self.auto_call_settle_input.setRange(0.0, 10.0)
        self.auto_call_settle_input.setSingleStep(0.1)
        self.auto_call_settle_input.setDecimals(1)
        self.auto_call_settle_input.setSuffix(" s")
        self.auto_call_settle_input.setFixedWidth(76)
        self.auto_call_settle_input.setValue(float(self.app_config.get("auto_call_settle_seconds", 1.6)))
        self.auto_call_settle_input.valueChanged.connect(self.auto_timing_changed)

        self.capture_first_threshold_input = QSpinBox()
        self.capture_first_threshold_input.setRange(1, 200)
        self.capture_first_threshold_input.setFixedWidth(70)
        self.capture_first_threshold_input.setValue(int(self.app_config.get("capture_first_detection_threshold", 35)))
        self.capture_first_threshold_input.valueChanged.connect(self.capture_threshold_changed)

        self.capture_second_threshold_input = QSpinBox()
        self.capture_second_threshold_input.setRange(1, 200)
        self.capture_second_threshold_input.setFixedWidth(70)
        self.capture_second_threshold_input.setValue(int(self.app_config.get("capture_second_detection_threshold", 75)))
        self.capture_second_threshold_input.valueChanged.connect(self.capture_threshold_changed)

        self.delete_logs_button = QPushButton("Deletar logs")
        self.delete_logs_button.clicked.connect(self.delete_all_logs)

        self.options_button = QToolButton()
        self.options_button.setText("Opcoes O")
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
        self.terminal_window = TerminalWindow()
        self.game_summary_log = self.terminal_window.browser
        self.summary_scroll_timer = QTimer(self)
        self.summary_scroll_timer.setSingleShot(True)
        self.summary_scroll_timer.timeout.connect(self.flush_pending_game_summary)
        summary_scroll_bar = self.game_summary_log.verticalScrollBar()
        summary_scroll_bar.sliderPressed.connect(self.mark_summary_scroll_active)
        summary_scroll_bar.sliderReleased.connect(self.flush_pending_game_summary)
        summary_scroll_bar.valueChanged.connect(self.mark_summary_scroll_active)

        self.debug_log = QPlainTextEdit()
        self.debug_log.setReadOnly(True)
        self.debug_log.setMaximumHeight(95)
        self.debug_log.document().setMaximumBlockCount(250)
        self.debug_log.setPlaceholderText("Logs tecnicos aparecem aqui com DEBUG ligado.")
        self.debug_log.setVisible(False)

        self.save_button.setText("Shot F3")
        self.annotator_button.setText("Anotar N")
        self.training_button.setText("Treino T")
        self.annotator_button.setMaximumWidth(78)
        self.training_button.setMaximumWidth(74)
        self.region_config_button.setMaximumWidth(74)
        self.routine_config_button.setMaximumWidth(82)
        self.terminal_button.setText("Terminal M")
        self.region_config_button.setText("Areas E")
        self.routine_config_button.setText("Rotinas")
        self.terminal_button.setMaximumWidth(86)

        controls = QHBoxLayout()
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setSpacing(4)
        controls.addWidget(QLabel("Jan:"))
        controls.addWidget(self.window_combo, stretch=3)
        controls.addWidget(self.refresh_button)
        controls.addWidget(QLabel("Mod:"))
        controls.addWidget(self.model_combo, stretch=4)
        controls.addWidget(self.mode_control(self.predict_checkbox, ""))
        controls.addWidget(self.mode_control(self.auto_play_checkbox, "Tab"))
        controls.addWidget(self.mode_control(self.preview_checkbox, "V"))
        controls.addWidget(self.mode_control(self.capture_mode_checkbox, "C"))
        controls.addWidget(self.mode_control(self.routines_checkbox, "R"))
        controls.addWidget(self.mode_control(self.show_regions_checkbox, "A"))
        controls.addWidget(self.options_button)
        controls.addWidget(self.annotator_button)
        controls.addWidget(self.training_button)
        controls.addWidget(self.region_config_button)
        controls.addWidget(self.routine_config_button)
        controls.addWidget(self.terminal_button)
        f3_label = QLabel("F3 salva")
        f3_label.setStyleSheet("QLabel { color: #94A3B8; font-size: 10px; }")
        controls.addWidget(f3_label)

        root_layout = QVBoxLayout()
        root_layout.setContentsMargins(4, 4, 4, 4)
        root_layout.setSpacing(4)
        root_layout.addLayout(controls)
        root_layout.addWidget(self.preview_label, stretch=1)
        root_layout.addWidget(self.debug_state_label)
        root_layout.addWidget(self.debug_log)

        root = QWidget()
        root.setLayout(root_layout)
        self.setCentralWidget(root)
        self.setStatusBar(QStatusBar())
        self.install_main_shortcuts()

        self.timer = QTimer(self)
        self.timer.setInterval(self.refresh_interval_ms)
        self.timer.timeout.connect(self.capture_once)

        self.refresh_model_list()
        self.refresh_windows()
        self.refresh_timer_interval()
        self.timer.start()
        self.capture_mode_changed()
        self.set_game_summary(self.last_game_summary)
        self.debug_setting_changed()
        self.log_debug("App iniciado.")

    def mode_control(self, checkbox: QCheckBox, shortcut: str) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(checkbox, alignment=Qt.AlignmentFlag.AlignHCenter)
        if shortcut:
            label = QLabel(shortcut)
            label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            label.setStyleSheet("QLabel { color: #94A3B8; font-size: 9px; margin-top: -3px; }")
            layout.addWidget(label)
        return container

    def install_main_shortcuts(self) -> None:
        bindings = {
            "Tab": self.toggle_auto_play,
            "V": lambda: self.toggle_checkbox_shortcut(self.preview_checkbox),
            "C": lambda: self.toggle_checkbox_shortcut(self.capture_mode_checkbox),
            "R": lambda: self.toggle_checkbox_shortcut(self.routines_checkbox),
            "A": lambda: self.toggle_checkbox_shortcut(self.show_regions_checkbox),
            "O": self.open_options_menu,
            "N": self.open_annotator,
            "T": self.open_training,
            "E": self.open_region_config,
            "M": self.open_terminal,
        }
        self.main_shortcuts = []
        for sequence, callback in bindings.items():
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)
            shortcut.activated.connect(callback)
            self.main_shortcuts.append(shortcut)

    @staticmethod
    def toggle_checkbox_shortcut(checkbox: QCheckBox) -> None:
        checkbox.setChecked(not checkbox.isChecked())

    def open_options_menu(self) -> None:
        menu = self.options_button.menu()
        if menu is None:
            return
        self.show()
        self.raise_()
        self.activateWindow()
        menu.popup(self.options_button.mapToGlobal(self.options_button.rect().bottomLeft()))

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
        layout.addRow("Apos call", self.auto_call_settle_input)
        layout.addRow("Capt 1a foto", self.capture_first_threshold_input)
        layout.addRow("Capt 2a foto", self.capture_second_threshold_input)
        layout.addRow("Logs", self.delete_logs_button)
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
        self.apply_game_summary_html(
            "<html><body style='background:#0B1220;color:#E5E7EB;font-family:Segoe UI,Arial,sans-serif;'>"
            f"<pre style='white-space:pre-wrap;font-size:12px;'>{escape(summary)}</pre>"
            "</body></html>"
        )

    def set_game_summary_state(self, state: HandState) -> None:
        summary = state.summary()
        self.last_game_summary = summary
        self.apply_game_summary_html(self.game_summary_html(state))

    def apply_game_summary_html(self, html: str) -> None:
        if html == self.last_game_summary_html:
            return
        scroll_bar = self.game_summary_log.verticalScrollBar()
        if self.summary_scroll_is_active():
            self.pending_game_summary_html = html
            return
        scroll_value = scroll_bar.value()
        self.last_game_summary_html = html
        self.game_summary_log.setHtml(html)
        self.restore_summary_scroll(scroll_value)
        if self.terminal_window.isVisible():
            self.game_summary_log.repaint()

    def flush_pending_game_summary(self) -> None:
        if not self.pending_game_summary_html:
            return
        if self.summary_scroll_is_active():
            self.summary_scroll_timer.start(650)
            return
        html = self.pending_game_summary_html
        self.pending_game_summary_html = None
        self.apply_game_summary_html(html)

    def mark_summary_scroll_active(self, *_args) -> None:
        if not self.terminal_window.isVisible():
            return
        self.summary_scroll_active_until = time.perf_counter() + 0.6
        self.summary_scroll_timer.start(650)

    def summary_scroll_is_active(self) -> bool:
        if not self.terminal_window.isVisible():
            return False
        scroll_bar = self.game_summary_log.verticalScrollBar()
        return scroll_bar.isSliderDown() or time.perf_counter() < self.summary_scroll_active_until

    def restore_summary_scroll(self, value: int) -> None:
        def apply_scroll() -> None:
            scroll_bar = self.game_summary_log.verticalScrollBar()
            scroll_bar.setValue(min(value, scroll_bar.maximum()))

        QTimer.singleShot(0, apply_scroll)

    def game_summary_html(self, state: HandState) -> str:
        dora = "".join(self.tile_chip(tile) for tile in state.dora_tiles[:5]) or self.empty_chip("-")
        indicators = "".join(self.tile_chip(tile) for tile in state.dora_indicators[:5]) or self.empty_chip("-")
        hand_tiles = "".join(self.tile_chip(tile) for tile in state.hand_tiles)
        if state.missing_count:
            hand_tiles += "".join(self.empty_chip("???") for _ in range(state.missing_count))
        melds = "".join(self.meld_chip(meld) for meld in state.open_melds) or self.empty_chip("-")
        yaku_cards = self.plans_compact_table(state.likely_yaku[:7])
        needs = self.needs_table(state.helpful_missing_tiles[:8])
        discard_cards = "".join(self.discard_card(explanation) for explanation in state.discard_explanations)
        if not discard_cards:
            discard_cards = "<div class='muted'>Ainda sem descarte recomendado.</div>"
        furiten_box = self.furiten_box(state)
        progress_box = self.progress_box(state)
        value_box = self.value_box(state)
        strategy_box = self.strategy_box(state)
        calls = "".join(self.call_chip(decision) for decision in state.call_decisions)
        if not calls and not (state.chii_button_visible or state.pon_button_visible or state.kan_button_visible):
            calls = "<div class='muted'>Nenhum botao de chamada ativo.</div>"
        discarded_seen = self.discarded_seen_box(state)
        signals = self.signals_box(state, show_score_ocr=self.show_regions_checkbox.isChecked())
        routines = self.routines_box()

        winds = " ".join(
            f"<span class='pill'>{label}: {escape(str(state.player_winds.get(player, '?')))}</span>"
            for player, label in (("principal", "P"), ("esquerda", "E"), ("cima", "C"), ("direita", "D"))
        )
        warning = ""
        if state.missing_count:
            warning = (
                f"<div class='warn'>Leitura incompleta: faltam {state.missing_count} peca(s); "
                "a recomendacao pode oscilar.</div>"
            )

        return f"""
        <html>
        <head>
        <style>
            body {{ margin:0; background:#0B1220; color:#D7DEE9; font-family:'Segoe UI', Arial, sans-serif; }}
            .wrap {{ padding:4px; overflow:hidden; }}
            .top {{ margin-bottom:4px; font-size:11px; }}
            .pill {{ background:#101826; border:1px solid #2A3648; border-radius:3px; padding:1px 4px; color:#C8D1DF; }}
            .panel {{ background:#111827; border:1px solid #1F2937; border-radius:4px; padding:5px; margin-bottom:4px; }}
            .grid {{ width:100%; table-layout:fixed; }}
            .cell {{ vertical-align:top; width:50%; overflow:hidden; }}
            .title {{ color:#F3F6FB; font-size:12px; font-weight:700; margin-bottom:3px; }}
            .muted {{ color:#94A3B8; font-size:11px; }}
            .warn {{ background:#172033; padding:4px 6px; margin-bottom:4px; color:#E9DFA8; }}
            .tile {{ display:inline-block; background:#101826; border:1px solid #334155; border-radius:3px; padding:1px 4px; margin:0 2px 2px 0; font-weight:700; white-space:nowrap; }}
            .empty {{ display:inline-block; color:#94A3B8; border:1px dashed #475569; border-radius:3px; padding:1px 4px; margin:0 2px 2px 0; white-space:nowrap; }}
            .meld {{ background:#0F172A; border:1px solid #334155; border-radius:3px; padding:2px 4px; }}
            .discard {{ margin-bottom:5px; border:1px solid #334155; border-radius:4px; }}
            .badge {{ font-size:10px; font-weight:800; padding:2px 4px; border-radius:3px; }}
            .badge.red {{ background:#3A1820; color:#FCA5A5; border:1px solid #7F1D1D; }}
            .badge.yellow {{ background:#332A14; color:#FDE68A; border:1px solid #854D0E; }}
            .badge.blue {{ background:#10263A; color:#93C5FD; border:1px solid #1D4ED8; }}
            .meter {{ height:5px; background:#1E293B; }}
            .fill {{ height:5px; background:#D15E68; }}
            .score-box {{ margin-top:3px; padding:4px; background:#0F172A; border:1px solid #263447; border-radius:4px; }}
            .score-main {{ color:#E7F0FF; font-weight:800; }}
            .discard-body {{ padding:4px 6px; font-size:11px; color:#CBD5E1; }}
            .protect {{ color:#9DDBC5; }}
            .reason {{ color:#DFA3A3; }}
            .stat {{ color:#91B7E8; }}
            .bar {{ margin:4px 0; }}
            .bar-label {{ font-size:11px; color:#CBD5E1; }}
            .bar-fill {{ height:7px; }}
            .plan-row {{ border-bottom:1px solid #1F2937; }}
            .plan-score {{ color:#CFE3FF; font-weight:700; }}
            .need-row {{ border-bottom:1px solid #1F2937; }}
            .call {{ background:#0F172A; border:1px solid #334155; border-radius:5px; padding:5px 7px; }}
            .mini-row {{ border-bottom:1px solid #1F2937; font-size:11px; }}
            .ok {{ color:#86EFAC; font-weight:700; }}
            .bad {{ color:#FCA5A5; font-weight:700; }}
        </style>
        </head>
        <body><div class='wrap'>
            <div class='top'>
                <span class='pill'>{len(state.all_known_tiles)}/{state.expected_visible_min} pecas</span>
                {winds}
                <span class='pill'>Dora {dora}</span>
                <span class='pill'>Indic {indicators}</span>
            </div>
            {warning}
            <table class='grid' cellspacing='4' cellpadding='0'>
                <tr>
                    <td class='cell'>
                        <div class='panel'><div class='title'>Mao</div>{hand_tiles}<div class='title' style='margin-top:5px;'>Abertas</div>{melds}</div>
                        <div class='panel'><div class='title'>Descartes detectados</div>{discarded_seen}</div>
                        <div class='panel'><div class='title'>Descartes marcados no overlay</div>{furiten_box}{discard_cards}</div>
                    </td>
                    <td class='cell'>
                        <div class='panel'>{progress_box}{value_box}{strategy_box}<div class='title' style='margin-top:5px;'>Planos ativos</div>{yaku_cards}</div>
                        <div class='panel'><div class='title'>Pecas que ajudam</div>{needs}</div>
                        <div class='panel'><div class='title'>Sinais</div>{signals}{routines}<div class='title' style='margin-top:5px;'>Chamadas</div>{calls}</div>
                    </td>
                </tr>
            </table>
        </div></body></html>
        """

    def tile_chip(self, tile: Tile) -> str:
        colors = {
            "man": "#F0A6A6",
            "pin": "#A9C7F5",
            "sou": "#A7D9B8",
            "wind": "#D9C78B",
            "dragon": "#D7A6CC",
        }
        accent = colors.get(tile.suit, "#CBD5E1")
        return (
            f"<span class='tile' style='color:{accent};'>"
            f"&nbsp;{escape(tile.compact)}&nbsp;</span>&nbsp;"
        )

    def empty_chip(self, text: str) -> str:
        return f"<span class='empty'>&nbsp;{escape(text)}&nbsp;</span>&nbsp;"

    def meld_chip(self, meld) -> str:
        tiles = "".join(self.tile_chip(tile) for tile in meld.tiles)
        return f"<span class='meld'><b>{escape(str(meld.kind.value))}</b>&nbsp;{tiles}</span>&nbsp;"

    def plans_compact_table(self, matches) -> str:
        if not matches:
            return "<div class='muted'>Sem yaku confiavel ainda.</div>"
        rows = []
        for match in matches:
            rows.append(
                "<tr class='plan-row'>"
                f"<td style='color:#CBD5E1;padding:1px 0;'>{escape(match.yaku.name)}</td>"
                f"<td class='plan-score' align='right' width='44'>{max(0, min(100, int(match.confidence)))}%</td>"
                "</tr>"
            )
        return "<table width='100%' cellspacing='0' cellpadding='1'>" + "".join(rows) + "</table>"

    def progress_box(self, state: HandState) -> str:
        progress = hand_completion_progress(state)
        return (
            "<table width='100%' cellspacing='0' cellpadding='0'><tr>"
            "<td width='92'><b>Fechar</b></td>"
            f"<td>{self.segmented_progress_bar(progress.percent)}</td>"
            f"<td width='78' align='right'><b>{progress.percent}%</b> <span class='muted'>{escape(progress.label)}</span></td>"
            "</tr></table>"
        )

    def segmented_progress_bar(self, percent: int) -> str:
        total = 20
        filled = max(0, min(total, round(total * max(0, min(100, int(percent))) / 100)))
        cells = []
        for index in range(total):
            color = "#38BDF8" if index < filled else "#1E293B"
            cells.append(f"<td bgcolor='{color}' width='5%' height='8'></td>")
        return "<table width='100%' cellspacing='1' cellpadding='0'><tr>" + "".join(cells) + "</tr></table>"

    def value_box(self, state: HandState) -> str:
        value = estimated_hand_value(state)
        yakus = ", ".join(f"{name} {han}h" for name, han in value.yakus) or "sem yaku confirmado"
        bonuses = ", ".join(f"{name} +{han}" for name, han in value.bonus_items)
        bonus_text = f" | Bonus: {escape(bonuses)}" if bonuses else ""
        if value.ron_points <= 0:
            points = "<span class='muted'>sem yaku confirmado</span>"
        else:
            limit = f" {value.limit_name}" if value.limit_name else ""
            points = (
                f"<span class='score-main'>{value.han} han / {value.fu} fu{escape(limit)}</span>"
                f" | Ron {value.ron_points} | Tsumo {escape(value.tsumo_points)}"
            )
        return (
            "<div class='score-box'>"
            f"<div><b>Valor</b>: {points}</div>"
            f"<div class='muted'>Yakus: {escape(yakus)}{bonus_text}</div>"
            "</div>"
        )

    def needs_table(self, needs) -> str:
        if not needs:
            return self.empty_chip("-")
        rows = []
        for need in needs:
            rows.append(
                "<tr class='need-row'>"
                f"<td width='72'>{self.tile_chip(need.tile)}</td>"
                f"<td width='42' align='right'><b>{need.score}%</b></td>"
                f"<td style='color:#94A3B8;'>&nbsp;{escape(need.reason)}</td>"
                "</tr>"
            )
        return "<table width='100%' cellspacing='0' cellpadding='2'>" + "".join(rows) + "</table>"

    def furiten_box(self, state: HandState) -> str:
        if not state.current_furiten_waits and not state.furiten_waits:
            return ""
        current = "".join(
            f"<div class='reason'>Atual: {self.tile_chip(need.tile)} {escape(need.reason)}</div>"
            for need in state.current_furiten_waits
        )
        future = "".join(
            f"<div class='reason'>Risco: {self.tile_chip(need.tile)} {escape(need.reason)}</div>"
            for need in state.furiten_waits
        )
        return (
            "<div style='background:#28171B;border:1px solid #7F1D1D;padding:6px;margin-bottom:8px;'>"
            "<b style='color:#FCA5A5;'>Furiten</b>"
            f"{current}{future}"
            "</div>"
        )

    def strategy_box(self, state: HandState) -> str:
        objective = state.attack_objective
        if objective is None:
            target = "<span class='muted'>sem alvo de ataque</span>"
        else:
            wanted = ", ".join(objective.wanted[:5]) or "-"
            target = (
                f"<b>{escape(objective.name)}</b> {objective.probability}% | "
                f"{objective.han} han | score {objective.score}"
                f"<div class='muted'>Faltam/interessam: {escape(wanted)}</div>"
            )
        mode_color = {
            "attack": "#86EFAC",
            "balanced": "#FDE68A",
            "cautious": "#FDBA74",
            "defense": "#93C5FD",
        }.get(state.strategy_mode, "#86EFAC")
        return (
            "<div class='score-box'>"
            f"<div><b>Estrategia</b>: <span style='color:{mode_color};'>{escape(state.strategy_mode.upper())}</span></div>"
            f"<div>{target}</div>"
            f"<div class='muted'>{escape(state.strategy_reason or '-')}</div>"
            "</div>"
        )

    def discarded_seen_box(self, state: HandState) -> str:
        rows = []
        labels = {
            "principal": "Voce",
            "esquerda": "Esq",
            "cima": "Cima",
            "direita": "Dir",
        }
        for player in ("principal", "esquerda", "cima", "direita"):
            tiles = state.discarded_by_player.get(player, [])
            chips = "".join(self.tile_chip(tile) for tile in tiles[-12:]) or self.empty_chip("-")
            rows.append(
                "<tr class='mini-row'>"
                f"<td width='48'><b>{labels[player]}</b></td>"
                f"<td>{chips}</td>"
                f"<td width='28' align='right' class='muted'>{len(tiles)}</td>"
                "</tr>"
            )
        total = sum(len(tiles) for tiles in state.discarded_by_player.values())
        return (
            f"<div class='muted' style='margin-bottom:3px;'>Total detectado: {total}</div>"
            "<table width='100%' cellspacing='0' cellpadding='2'>"
            + "".join(rows)
            + "</table>"
        )

    def signals_box(self, state: HandState, show_score_ocr: bool = False) -> str:
        actions = [
            ("Chii", state.chii_button_visible),
            ("Pon", state.pon_button_visible),
            ("Kan", state.kan_button_visible),
            ("Riichi", state.riichi_button_visible),
        ]
        action_rows = "".join(
            "<tr class='mini-row'>"
            f"<td>{escape(label)}</td>"
            f"<td width='44' align='right' class='{'ok' if active else 'muted'}'>{'sim' if active else 'nao'}</td>"
            "</tr>"
            for label, active in actions
        )

        statuses = [
            status
            for status in self.pixel_trigger_statuses(self.last_capture)
            if str(status.get("key", "")).startswith("riichi_stick")
        ]
        if statuses:
            riichi_rows = "".join(
                "<tr class='mini-row'>"
                f"<td>{escape(str(status.get('label', status.get('key', 'Richi'))))}</td>"
                f"<td width='44' align='right' class='{'ok' if status.get('positive') else 'muted'}'>"
                f"{'sim' if status.get('positive') else 'nao'}</td>"
                f"<td width='58' align='right' class='muted'>{escape(str(status.get('current_color', '-')))}</td>"
                "</tr>"
                for status in statuses
            )
        else:
            riichi_rows = "<tr class='mini-row'><td colspan='3' class='muted'>Richi por pixel sem leitura ativa.</td></tr>"

        score_section = ""
        if show_score_ocr:
            score_rows = "".join(
                "<tr class='mini-row'>"
                f"<td>{escape(str(score.get('label', player)))}</td>"
                f"<td width='70' align='right' class='{'ok' if score.get('value') is not None else 'muted'}'>"
                f"{escape(str(score.get('value') if score.get('value') is not None else score.get('text') or '-'))}</td>"
                "</tr>"
                for player, score in self.latest_score_ocr.items()
            ) or "<tr class='mini-row'><td colspan='2' class='muted'>OCR sem leitura.</td></tr>"
            score_section = (
                "<div class='title' style='margin-top:5px;'>Pontuacao OCR</div>"
                "<table width='100%' cellspacing='0' cellpadding='2'>"
                + score_rows
                + "</table>"
            )

        riichi_mark_rows = "".join(
            "<tr class='mini-row'>"
            f"<td>{escape(player)}</td>"
            f"<td width='80' align='right' class='muted'>apos {state.riichi_discard_marks.get(player, 0)}</td>"
            "</tr>"
            for player in state.riichi_players
        ) or "<tr class='mini-row'><td colspan='2' class='muted'>Nenhum Riichi adversario marcado.</td></tr>"

        return (
            "<table width='100%' cellspacing='0' cellpadding='2'>"
            + action_rows
            + "</table>"
            + score_section
            + "<div class='title' style='margin-top:5px;'>Marcas de Riichi</div>"
            "<table width='100%' cellspacing='0' cellpadding='2'>"
            + riichi_mark_rows
            + "</table>"
            + "<div class='title' style='margin-top:5px;'>Richi pixel</div>"
            "<table width='100%' cellspacing='0' cellpadding='2'>"
            + riichi_rows
            + "</table>"
        )

    def routines_box(self) -> str:
        if not self.routines_checkbox.isChecked():
            return ""
        if not self.latest_routine_statuses:
            return (
                "<div class='title' style='margin-top:5px;'>Rotinas</div>"
                "<div class='muted'>Rot ligada; aguardando leitura de pixels.</div>"
            )
        rows = []
        for item in self.latest_routine_statuses[:8]:
            status = "SIM" if item.get("positive") else "nao"
            css = "ok" if item.get("positive") else "muted"
            rows.append(
                "<tr class='mini-row'>"
                f"<td>{escape(str(item.get('routine', '-')))} {int(item.get('step', 0)) + 1}/{int(item.get('total', 1))}</td>"
                f"<td>{escape(str(item.get('role', '-')))}</td>"
                f"<td class='{css}' align='right'>{status}</td>"
                f"<td class='muted' align='right'>d{escape(str(item.get('distance', '-')))}"
                f"/t{escape(str(item.get('tolerance', '-')))}</td>"
                f"<td class='muted'>{escape(str(item.get('current_color', '-')))}</td>"
                "</tr>"
            )
        return (
            "<div class='title' style='margin-top:5px;'>Rotinas</div>"
            "<table width='100%' cellspacing='0' cellpadding='2'>"
            + "".join(rows)
            + "</table>"
        )

    def discard_card(self, explanation) -> str:
        badge_names = {"red": "VERMELHA", "yellow": "AMARELA", "blue": "DEFESA"}
        badge = badge_names.get(explanation.color, explanation.color.upper())
        reason_items = "".join(f"<div class='reason'>- {escape(reason)}</div>" for reason in explanation.reasons)
        safeguard_items = "".join(f"<div class='protect'>+ {escape(reason)}</div>" for reason in explanation.safeguards)
        stats = (
            f"<span class='stat'>visiveis {explanation.visible_count}/4</span> | "
            f"<span class='stat'>nao vistas {explanation.remaining_count}</span> | "
            f"<span class='stat'>shanten apos {explanation.shanten_after}</span> | "
            f"<span class='stat'>compras {explanation.ukeire_after}</span>"
        )
        return (
            f"<div class='discard {explanation.color}'>"
            "<table width='100%' cellspacing='0' cellpadding='6' style='background:#0F172A;'><tr>"
            f"<td width='82'><span class='badge {explanation.color}'>{badge}</span></td>"
            f"<td width='110'>{self.tile_chip(explanation.tile)}</td>"
            f"<td><b>pressao {explanation.pressure}%</b></td>"
            "</tr></table>"
            f"<div class='meter'><div class='fill' style='width:{explanation.pressure}%;'></div></div>"
            f"<div class='discard-body'>{stats}{reason_items}{safeguard_items}</div>"
            "</div>"
        )

    def call_chip(self, decision: CallDecision) -> str:
        color = "#22C55E" if decision.recommended else "#EF4444"
        verdict = "OK" if decision.recommended else "SKIP"
        return (
            f"<span class='call' style='border-color:{color};'>"
            f"<b style='color:{color};'>{escape(verdict)}</b> {escape(decision.action)} "
            f"{decision.confidence}%<br><small>{escape(decision.reason)}</small></span>"
        )

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

    def set_preview_image(self, image: QImage) -> None:
        pixmap = QPixmap.fromImage(image)
        scaled = pixmap.scaled(
            self.preview_label.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.preview_label.setPixmap(scaled)

    def refresh_score_ocr(self, image: QImage) -> None:
        now = time.perf_counter()
        if self.score_ocr_last_at and now - self.score_ocr_last_at < SCORE_OCR_INTERVAL_SECONDS:
            return
        self.score_ocr_last_at = now
        self.latest_score_ocr = self.read_score_areas(image)
        first_error = next(
            (
                str(score.get("error"))
                for score in self.latest_score_ocr.values()
                if score.get("error")
            ),
            "",
        )
        if first_error and not self.score_ocr_error_logged:
            self.score_ocr_error_logged = True
            self.log_debug(f"OCR pontuacao indisponivel: {first_error}")

    def maybe_refresh_score_ocr_for_state(self, image: QImage, state: HandState) -> None:
        _ = state
        self.refresh_score_ocr(image)

    def read_score_areas(self, image: QImage) -> dict[str, dict]:
        players = {
            "principal": ("score_player", "Jogador", 0),
            "esquerda": ("score_left", "Esquerda", 270),
            "cima": ("score_top", "Frente", 180),
            "direita": ("score_right", "Direita", 90),
        }
        scores: dict[str, dict] = {}
        for player, (region_key, label, rotation) in players.items():
            region = self.app_config.get("regions", {}).get(region_key)
            if not region or not region.get("enabled", True):
                continue
            rect = self.configured_region_rect(region_key)
            left = max(0, int(rect.left()))
            top = max(0, int(rect.top()))
            right = min(image.width(), int(rect.right()))
            bottom = min(image.height(), int(rect.bottom()))
            width = max(0, right - left)
            height = max(0, bottom - top)
            if width <= 0 or height <= 0:
                continue
            text, value, error = self.ocr_score_crop(image.copy(left, top, width, height), rotation)
            item = {"region": region_key, "label": label, "text": text, "value": value}
            if error:
                item["error"] = error
            scores[player] = item
        return scores

    @staticmethod
    def tesseract_executable_path() -> Path | None:
        candidates = [
            shutil.which("tesseract"),
            r"C:\Program Files\Tesseract-OCR\tesseract.exe",
            r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        ]
        for candidate in candidates:
            if not candidate:
                continue
            path = Path(candidate)
            if path.exists():
                return path
        return None

    def ocr_score_crop(self, crop: QImage, rotation: int) -> tuple[str, int | None, str]:
        tesseract_path = self.tesseract_executable_path()
        if tesseract_path is None:
            return "", None, "tesseract.exe nao encontrado"

        temp_path = ""
        try:
            with tempfile.NamedTemporaryFile(prefix="mahjong_score_", suffix=".png", delete=False) as temp_file:
                temp_path = temp_file.name

            saved_with_pillow = False
            try:
                from PIL import Image, ImageOps

                qimage = crop.convertToFormat(QImage.Format.Format_RGBA8888)
                buffer = qimage.bits()
                buffer.setsize(qimage.sizeInBytes())
                pil_image = Image.frombytes("RGBA", (qimage.width(), qimage.height()), bytes(buffer), "raw", "RGBA")
                if rotation:
                    pil_image = pil_image.rotate(rotation, expand=True)
                pil_image = pil_image.convert("L")
                pil_image = ImageOps.autocontrast(pil_image)
                pil_image = pil_image.resize((pil_image.width * 3, pil_image.height * 3))
                pil_image = pil_image.point(lambda value: 255 if value > 115 else 0)
                pil_image.save(temp_path)
                saved_with_pillow = True
            except Exception:
                qimage = crop
                if rotation:
                    qimage = qimage.transformed(QTransform().rotate(rotation))
                qimage.save(temp_path, "PNG")

            env = os.environ.copy()
            tessdata_dir = tesseract_path.parent / "tessdata"
            if tessdata_dir.exists():
                env["TESSDATA_PREFIX"] = str(tessdata_dir)
            completed = subprocess.run(
                [
                    str(tesseract_path),
                    temp_path,
                    "stdout",
                    "--oem",
                    "3",
                    "--psm",
                    "7",
                    "-c",
                    "tessedit_char_whitelist=0123456789",
                ],
                capture_output=True,
                text=True,
                timeout=3.0,
                env=env,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if completed.returncode != 0:
                detail = (completed.stderr or completed.stdout or "erro desconhecido").strip().replace("\n", " ")
                return "", None, f"tesseract falhou: {detail[:140]}"
            digits = "".join(char for char in completed.stdout if char.isdigit())
            if not digits and saved_with_pillow:
                return "", None, "sem digitos lidos"
            return digits, int(digits) if digits else None, ""
        except Exception as error:
            return "", None, f"{type(error).__name__}: {error}"
        finally:
            if temp_path:
                try:
                    Path(temp_path).unlink(missing_ok=True)
                except OSError:
                    pass

    def capture_once(self, *_args, force: bool = False) -> None:
        if (
            not force
            and not self.preview_checkbox.isChecked()
            and not self.predict_checkbox.isChecked()
            and not (self.routines_checkbox.isChecked() and self.auto_play_checkbox.isChecked())
        ):
            self.preview_label.setPixmap(QPixmap())
            self.preview_label.setText("Preview desabilitado. Ative Preview, Predict ou Rot para monitorar.")
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
        if self.routines_checkbox.isChecked() and self.auto_play_checkbox.isChecked():
            self.latest_routine_statuses = self.routine_statuses(image)
            self.maybe_log_routine_statuses()
        if self.routines_checkbox.isChecked() and self.auto_play_checkbox.isChecked():
            self.handle_routines(image)
        if self.predict_checkbox.isChecked() and not self.predict_runtime_error:
            self.submit_prediction(image, source="timer")
        display_image = image
        if self.show_regions_checkbox.isChecked() or self.routines_checkbox.isChecked():
            display_image = display_image.copy()
            if self.show_regions_checkbox.isChecked():
                self.draw_detection_regions(display_image)
                self.draw_score_ocr_overlay(display_image)
            if self.routines_checkbox.isChecked():
                self.draw_routine_debug_overlay(display_image)
        if self.preview_checkbox.isChecked():
            keep_rendered_frame = (
                self.predict_checkbox.isChecked()
                and self.predict_in_flight
                and self.preview_label.pixmap() is not None
                and not self.preview_label.pixmap().isNull()
            )
            if not keep_rendered_frame:
                pixmap = QPixmap.fromImage(display_image)
                scaled = pixmap.scaled(
                    self.preview_label.size(),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                self.preview_label.setPixmap(scaled)
        else:
            self.preview_label.setPixmap(QPixmap())
            if self.predict_checkbox.isChecked():
                self.preview_label.setText("Preview desabilitado. Predict continua rodando em segundo plano.")
            elif self.routines_checkbox.isChecked() and self.auto_play_checkbox.isChecked():
                self.preview_label.setText("Preview desabilitado. Rotinas continuam monitorando em segundo plano.")

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
            self.open_terminal()
            self.capture_once()
        else:
            self.log_debug("Predict desligado.")

    def auto_play_setting_changed(self, *_args) -> None:
        if not self.auto_play_checkbox.isChecked():
            self.finalize_auto_decision_log("autoplay_off")
            self.reset_routine_runtime_state()
        self.auto_turn_frames = 0
        self.reset_chii_option_tracker()
        for history in self.auto_decision_history.values():
            history.clear()
        if self.auto_play_checkbox.isChecked() and not self.predict_checkbox.isChecked():
            self.predict_checkbox.setChecked(True)
        if self.auto_play_checkbox.isChecked():
            self.start_auto_decision_log()
            if self.routines_checkbox.isChecked():
                self.statusBar().showMessage("AutoPlay ligado; rotinas agora podem executar.")
        state = "ligado" if self.auto_play_checkbox.isChecked() else "desligado"
        self.log_debug(f"Auto {state}.")
        self.refresh_debug_state()

    def toggle_auto_play(self) -> None:
        self.auto_play_checkbox.setChecked(not self.auto_play_checkbox.isChecked())
        state = "ligado" if self.auto_play_checkbox.isChecked() else "desligado"
        self.statusBar().showMessage(f"AutoPlay {state} pelo Tab.")

    def annotation_shortcuts_active(self) -> bool:
        annotator = self.annotator_window
        if annotator is None or not annotator.isVisible():
            return False
        active_window = QApplication.activeWindow()
        if active_window is annotator:
            return True
        focus_widget = QApplication.focusWidget()
        while focus_widget is not None:
            if focus_widget is annotator:
                return True
            focus_widget = focus_widget.parentWidget()
        return False

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Tab:
            if not self.predict_checkbox.isChecked():
                super().keyPressEvent(event)
                return
            self.toggle_auto_play()
            event.accept()
            return
        if event.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier):
            super().keyPressEvent(event)
            return
        toggles = {
            Qt.Key.Key_V: self.preview_checkbox,
            Qt.Key.Key_C: self.capture_mode_checkbox,
            Qt.Key.Key_R: self.routines_checkbox,
            Qt.Key.Key_A: self.show_regions_checkbox,
        }
        checkbox = toggles.get(event.key())
        if checkbox is not None:
            if checkbox is not self.predict_checkbox and not self.predict_checkbox.isChecked():
                super().keyPressEvent(event)
                return
            checkbox.setChecked(not checkbox.isChecked())
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
        self.auto_call_settle_seconds = self.auto_call_settle_input.value()
        self.app_config["auto_mouse_delay_min"] = mouse_min
        self.app_config["auto_mouse_delay_max"] = mouse_max
        self.app_config["auto_click_delay_min"] = click_min
        self.app_config["auto_click_delay_max"] = click_max
        self.app_config["auto_call_settle_seconds"] = self.auto_call_settle_seconds
        save_config(CONFIG_PATH, self.app_config)
        self.statusBar().showMessage(
            f"Auto: move {mouse_min:.1f}-{mouse_max:.1f}s; clique {click_min:.1f}-{click_max:.1f}s; "
            f"apos call {self.auto_call_settle_seconds:.1f}s."
        )

    def capture_threshold_changed(self, *_args) -> None:
        first_threshold = self.capture_first_threshold_input.value()
        second_threshold = self.capture_second_threshold_input.value()
        if first_threshold > second_threshold:
            sender = self.sender()
            if sender is self.capture_first_threshold_input:
                self.capture_second_threshold_input.blockSignals(True)
                self.capture_second_threshold_input.setValue(first_threshold)
                self.capture_second_threshold_input.blockSignals(False)
                second_threshold = first_threshold
            else:
                self.capture_first_threshold_input.blockSignals(True)
                self.capture_first_threshold_input.setValue(second_threshold)
                self.capture_first_threshold_input.blockSignals(False)
                first_threshold = second_threshold
        self.app_config["capture_first_detection_threshold"] = first_threshold
        self.app_config["capture_second_detection_threshold"] = second_threshold
        save_config(CONFIG_PATH, self.app_config)
        self.statusBar().showMessage(
            f"Captura: 1a foto com {first_threshold} deteccoes; 2a foto com {second_threshold} deteccoes."
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

    def capture_mode_changed(self, *_args) -> None:
        enabled = self.capture_mode_checkbox.isChecked()
        self.app_config["capture_mode_enabled"] = enabled
        save_config(CONFIG_PATH, self.app_config)
        if enabled:
            self.capture_threshold_stage = 1
            self.capture_waiting_for_reset = False
            if not self.predict_checkbox.isChecked():
                self.predict_checkbox.setChecked(True)
            self.statusBar().showMessage(
                f"Modo captura ligado: aguardando {self.capture_first_threshold_input.value()} deteccoes."
            )
            self.log_debug("Modo captura ligado.")
        else:
            self.log_debug("Modo captura desligado.")

    def routines_mode_changed(self, *_args) -> None:
        enabled = self.routines_checkbox.isChecked()
        self.app_config["routines_enabled"] = enabled
        save_config(CONFIG_PATH, self.app_config)
        if enabled:
            self.statusBar().showMessage("Rotinas ligadas: triggers de pixel ativos.")
            self.log_debug("Rotinas ligadas.")
            self.capture_once(force=True)
        else:
            self.reset_routine_runtime_state()
            self.log_debug("Rotinas desligadas.")

    def reset_routine_runtime_state(self) -> None:
        self.routine_click_pending = False
        self.routine_step_index.clear()
        self.routine_step_repeat_count.clear()
        self.routine_last_click_at.clear()
        self.routine_step_ready_since.clear()
        self.latest_routine_statuses = []

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
        self.predict_in_flight = False
        self.pending_prediction_images.clear()
        self.prediction_unload_requested.emit()
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
        if not self.submit_prediction(self.last_capture, source="manual"):
            self.statusBar().showMessage("Predict 1x aguardando o predict atual terminar.")
        self.refresh_debug_state()

    def submit_prediction(self, image: QImage, source: str) -> bool:
        model_path = self.selected_model_path()
        if model_path is None:
            return False
        if self.predict_in_flight:
            return False

        chii_button_visible = self.detect_chii_button(image)
        chii_choose_visible = self.detect_chii_choose_header(image)
        pon_button_visible = self.detect_pon_button(image)
        kan_button_visible = self.detect_kan_button(image)
        riichi_button_visible = self.detect_riichi_button(image)
        win_button_visible = self.detect_win_button(image)
        chii_prompt_visible = chii_button_visible or chii_choose_visible
        call_source_player = "esquerda" if chii_prompt_visible else self.detect_call_source_player(image)
        player_winds = self.detect_player_winds(image)

        self.predict_request_id += 1
        request_id = self.predict_request_id
        self.predict_in_flight = True
        self.pending_prediction_images[request_id] = image.copy()
        context = {
            "source": source,
            "model_path": str(model_path),
            "imgsz": PREDICT_IMAGE_SIZE,
            "conf": self.predict_conf_input.value(),
            "device": self.prediction_device(),
            "capture_width": CAPTURE_WIDTH,
            "capture_height": CAPTURE_HEIGHT,
            "regions": dict(self.app_config["regions"]),
            "chii_button_visible": chii_prompt_visible,
            "pon_button_visible": pon_button_visible,
            "kan_button_visible": kan_button_visible,
            "riichi_button_visible": riichi_button_visible,
            "win_button_visible": win_button_visible,
            "chii_choose_visible": chii_choose_visible,
            "call_source_player": call_source_player,
            "player_winds": player_winds,
        }
        frame_array = self.qimage_to_rgb_array(image)
        self.log_debug(
            f"Predict enfileirado ({source}) | frame={image.width()}x{image.height()} | "
            f"conf={context['conf']:.2f} | device={context['device']}"
        )
        self.prediction_requested.emit(request_id, frame_array, context)
        return True

    def prediction_finished(self, request_id: int, result, detections, state: HandState, elapsed_ms: float) -> None:
        image = self.pending_prediction_images.pop(request_id, None)
        self.predict_in_flight = False
        if image is None:
            return

        riichi_button_visible = self.detect_riichi_button(image)
        win_button_visible = self.detect_win_button(image)
        tsumo_button_visible = self.detect_tsumo_button(image)
        chii_choose_visible = self.detect_chii_choose_header(image)
        call_source_player = "esquerda" if state.chii_button_visible else self.detect_call_source_player(image)
        self.enrich_state_with_table_signals(image, state)
        self.extend_terminal_action_decisions(state, riichi_button_visible, win_button_visible, tsumo_button_visible)
        self.update_game_state(state)
        self.latest_auto_detections = list(detections)
        self.latest_auto_state = state
        self.maybe_refresh_score_ocr_for_state(image, state)
        self.evaluate_capture_thresholds(image, len(detections))
        output = image.copy()
        self.draw_predictions(output, result, state)
        self.draw_call_indicators(output, state, call_source_player)
        if self.show_regions_checkbox.isChecked():
            self.draw_detection_regions(output)
        if self.routines_checkbox.isChecked():
            self.draw_routine_debug_overlay(output)
        if self.show_regions_checkbox.isChecked():
            self.draw_score_ocr_overlay(output)
        if self.preview_checkbox.isChecked() or not self.predict_checkbox.isChecked():
            self.set_preview_image(output)
        self.handle_auto_play(
            image,
            detections,
            state,
            riichi_button_visible,
            win_button_visible,
            tsumo_button_visible,
            chii_choose_visible,
        )
        self.predict_count += 1
        boxes = getattr(result, "boxes", None)
        detection_count = len(boxes) if boxes is not None else 0
        self.log_debug(f"Predict ok (worker) | {detection_count} deteccoes | {elapsed_ms:.0f} ms")
        self.statusBar().showMessage(
            f"Predict worker: {detection_count} deteccoes | {elapsed_ms:.0f} ms | "
            f"conf {self.predict_conf_input.value():.2f}"
        )

        if self.pause_after_predict_checkbox.isChecked():
            self.predict_checkbox.setChecked(False)
            self.pause_after_predict_checkbox.setChecked(False)
            self.statusBar().showMessage(
                f"Predict pausado apos 1 frame: {detection_count} deteccoes | {elapsed_ms:.0f} ms"
            )
        self.refresh_debug_state()

    def prediction_failed(self, request_id: int, message: str) -> None:
        self.pending_prediction_images.pop(request_id, None)
        self.predict_in_flight = False
        self.predict_runtime_error = f"Falha no predict: {message}"
        self.log_debug(self.predict_runtime_error)
        self.statusBar().showMessage(self.predict_runtime_error)
        if self.preview_checkbox.isChecked():
            self.preview_label.setText(self.predict_runtime_error)
        self.refresh_debug_state()

    def run_prediction(self, image: QImage, source: str) -> QImage:
        model = self.load_prediction_model()
        if model is None:
            return image

        try:
            started_at = time.perf_counter()
            device = self.prediction_device()
            chii_button_visible = self.detect_chii_button(image)
            chii_choose_visible = self.detect_chii_choose_header(image)
            pon_button_visible = self.detect_pon_button(image)
            kan_button_visible = self.detect_kan_button(image)
            riichi_button_visible = self.detect_riichi_button(image)
            win_button_visible = self.detect_win_button(image)
            chii_prompt_visible = chii_button_visible or chii_choose_visible
            call_source_player = "esquerda" if chii_prompt_visible else self.detect_call_source_player(image)
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
            chii_button_visible=chii_prompt_visible,
            pon_button_visible=pon_button_visible,
            kan_button_visible=kan_button_visible,
            call_source_player=call_source_player,
            player_winds=player_winds,
        )
        self.enrich_state_with_table_signals(image, state)
        tsumo_button_visible = self.detect_tsumo_button(image)
        self.extend_terminal_action_decisions(state, riichi_button_visible, win_button_visible, tsumo_button_visible)
        self.update_game_state(state)
        self.latest_auto_detections = list(detections)
        self.latest_auto_state = state
        self.maybe_refresh_score_ocr_for_state(image, state)
        self.evaluate_capture_thresholds(image, len(detections))
        output = image.copy()
        self.draw_predictions(output, result, state)
        self.draw_call_indicators(output, state, call_source_player)
        if self.show_regions_checkbox.isChecked():
            self.draw_detection_regions(output)
        if self.routines_checkbox.isChecked():
            self.draw_routine_debug_overlay(output)
        if self.show_regions_checkbox.isChecked():
            self.draw_score_ocr_overlay(output)
        self.handle_auto_play(
            image,
            detections,
            state,
            riichi_button_visible,
            win_button_visible,
            tsumo_button_visible,
            chii_choose_visible,
        )
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
        self.set_game_summary_state(state)
        self.log_debug(f"Estado de jogo: {state.summary()}")

    def extend_terminal_action_decisions(
        self,
        state: HandState,
        riichi_button_visible: bool,
        win_button_visible: bool,
        tsumo_button_visible: bool = False,
    ) -> None:
        state.riichi_button_visible = riichi_button_visible
        state.win_button_visible = bool(win_button_visible or tsumo_button_visible)
        if tsumo_button_visible:
            state.call_decisions.append(
                CallDecision("Tsumo", True, 100, "botao visivel", "vitoria detectada: clicar sempre")
            )
        if win_button_visible:
            state.call_decisions.append(
                CallDecision("Ron", True, 100, "botao visivel", "vitoria detectada: clicar sempre")
            )
        if riichi_button_visible:
            progress = hand_completion_progress(state)
            candidates = riichi_discard_candidates(state)
            recommended = state.is_closed and progress.shanten == 0 and bool(candidates)
            if not state.is_closed:
                reason = "mao aberta"
            elif state.missing_count:
                reason = f"leitura incompleta ({state.missing_count} peca(s) faltando)"
            elif progress.shanten != 0:
                reason = f"botao parece visivel, mas mao detectada esta {progress.label}"
            elif not candidates:
                reason = "nenhum descarte detectado deixa tenpai para Riichi"
            else:
                reason = "descartes validos para Riichi: " + ", ".join(tile.compact for tile in candidates[:4])
            state.call_decisions.append(
                CallDecision(
                    "Riichi",
                    recommended,
                    95 if recommended else 0,
                    "botao visivel",
                    reason,
                )
            )
    def detect_chii_button(self, image: QImage) -> bool:
        return self.active_button_probe(image, "Chii") is not None

    def detect_chii_choose_header(self, image: QImage) -> bool:
        return self.probe_matches(image, "chii_choose_header")

    def detect_pon_button(self, image: QImage) -> bool:
        return self.active_button_probe(image, "Pon") is not None

    def detect_kan_button(self, image: QImage) -> bool:
        return self.active_button_probe(image, "Kan") is not None

    def detect_riichi_button(self, image: QImage) -> bool:
        return self.active_button_probe(image, "Riichi") is not None

    def detect_win_button(self, image: QImage) -> bool:
        return self.detect_ron_button(image)

    def detect_tsumo_button(self, image: QImage) -> bool:
        return self.active_button_probe(image, "Tsumo") is not None

    def detect_ron_button(self, image: QImage) -> bool:
        return self.active_button_probe(image, "Ron") is not None

    def enrich_state_with_table_signals(self, image: QImage, state: HandState) -> None:
        self.update_riichi_discard_marks(image, state)
        state.opponent_win_signals = {}
        state.likely_yaku = likely_yaku(state)
        state.call_decisions = call_decisions_for_state(state)

    def update_riichi_discard_marks(self, image: QImage, state: HandState) -> None:
        active_players = set(self.detect_riichi_players(image))
        total_discards = sum(len(tiles) for tiles in state.discarded_by_player.values())
        if total_discards <= 2 and not active_players and (self.riichi_discard_marks or self.riichi_probe_hits):
            self.riichi_discard_marks.clear()
            self.riichi_probe_hits.clear()

        for player in ("principal", "esquerda", "cima", "direita"):
            if player in active_players:
                self.riichi_probe_hits[player] = self.riichi_probe_hits.get(player, 0) + 1
            else:
                self.riichi_probe_hits.pop(player, None)

        for player in active_players:
            if self.riichi_probe_hits.get(player, 0) < 3:
                continue
            if player != "principal" and len(state.discarded_by_player.get(player, [])) < 1:
                continue
            if player not in self.riichi_discard_marks:
                self.riichi_discard_marks[player] = len(state.discarded_by_player.get(player, []))
                self.log_debug(
                    f"Riichi detectado: {player} apos {self.riichi_discard_marks[player]} descarte(s)."
                )

        order = {"principal": 0, "esquerda": 1, "cima": 2, "direita": 3}
        state.riichi_players = tuple(sorted(self.riichi_discard_marks, key=lambda player: order.get(player, 99)))
        state.riichi_discard_marks = {
            player: max(0, min(len(state.discarded_by_player.get(player, [])), mark))
            for player, mark in self.riichi_discard_marks.items()
        }

    def detect_riichi_players(self, image: QImage) -> tuple[str, ...]:
        probes = {
            "principal": "riichi_stick_player",
            "esquerda": "riichi_stick_left",
            "cima": "riichi_stick_top",
            "direita": "riichi_stick_right",
        }
        return tuple(player for player, key in probes.items() if self.probe_matches(image, key))

    def detect_opponent_win_signals(self, image: QImage) -> dict[str, dict[str, bool]]:
        suffix_by_player = {
            "esquerda": "left",
            "cima": "top",
            "direita": "right",
        }
        signals: dict[str, dict[str, bool]] = {}
        for player, suffix in suffix_by_player.items():
            ron = self.probe_matches(image, f"opponent_ron_{suffix}")
            tsumo = self.probe_matches(image, f"opponent_tsumo_{suffix}")
            signals[player] = {"ron": ron, "tsumo": tsumo}
        return signals

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
        tsumo_button_visible: bool,
        chii_choose_visible: bool,
    ) -> None:
        self.remember_auto_context(image, detections, state, riichi_button_visible, win_button_visible)
        if not self.auto_play_checkbox.isChecked():
            self.auto_turn_frames = 0
            self.auto_last_discard_attempt = None
            self.auto_blocked_discard_keys.clear()
            self.auto_call_settle_until = 0.0
            self.auto_riichi_discard_until = 0.0
            self.reset_chii_option_tracker()
            for history in self.auto_decision_history.values():
                history.clear()
            return
        self.log_auto_decision_frame(detections, state, riichi_button_visible, win_button_visible, chii_choose_visible)
        self.update_auto_discard_failure_guard(state)

        if self.click_win_button_if_visible(win_button_visible, tsumo_button_visible):
            return

        visible_actions = {
            "Chii": state.chii_button_visible,
            "Pon": state.pon_button_visible,
            "Kan": state.kan_button_visible,
            "Riichi": riichi_button_visible,
        }
        if state.missing_count:
            visible_call_actions = [
                action
                for action in ("Kan", "Pon", "Chii")
                if visible_actions[action]
            ]
            if visible_call_actions and self.click_skip_button(visible_call_actions):
                actions_text = "/".join(visible_call_actions)
                self.log_debug(f"Auto: leitura incompleta; {actions_text} ignorado com Skip.")
                self.auto_last_click_at = time.perf_counter()
                self.auto_turn_frames = 0
                return
            if not state.discard_candidates:
                self.auto_turn_frames = 0
                self.statusBar().showMessage(
                    f"Auto: aguardando leitura melhor ({state.missing_count} peca(s) faltando)."
                )
                return
        recommended_actions = {
            decision.action
            for decision in state.call_decisions
            if decision.recommended
        }
        for action, visible in visible_actions.items():
            self.auto_decision_history[action].append(bool(visible and action in recommended_actions))

        if self.auto_click_pending or time.perf_counter() - self.auto_last_click_at < 0.8:
            return

        if chii_choose_visible:
            self.handle_chii_option_autoplay(detections, state)
            return
        self.reset_chii_option_tracker()

        if time.perf_counter() < self.auto_riichi_discard_until:
            if self.click_riichi_discard(detections, state):
                self.log_debug("Auto: agendou descarte valido para Riichi.")
                self.auto_last_click_at = time.perf_counter()
                self.auto_turn_frames = 0
            else:
                self.statusBar().showMessage("Auto: Riichi aceito; aguardando descarte valido detectavel.")
            return

        for action in ("Riichi", "Kan", "Pon", "Chii"):
            if visible_actions[action] and any(self.auto_decision_history[action]):
                if self.click_action_button(action):
                    self.log_debug(f"Auto: agendou clique em {action}.")
                    self.auto_last_click_at = time.perf_counter()
                    self.auto_turn_frames = 0
                return

        visible_call_actions = [
            action
            for action in ("Kan", "Pon", "Chii")
            if visible_actions[action]
        ]
        if visible_call_actions and not any(action in recommended_actions for action in visible_call_actions):
            if self.click_skip_button(visible_call_actions):
                actions_text = "/".join(visible_call_actions)
                self.log_debug(f"Auto: {actions_text} nao recomendado; agendou Skip.")
                self.auto_last_click_at = time.perf_counter()
                self.auto_turn_frames = 0
            return

        if time.perf_counter() < self.auto_call_settle_until:
            remaining = self.auto_call_settle_until - time.perf_counter()
            self.auto_turn_frames = 0
            self.statusBar().showMessage(f"Auto: aguardando mesa estabilizar apos call ({remaining:.1f}s).")
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

    def handle_chii_option_autoplay(self, detections: list, state: HandState) -> None:
        clusters = self.detect_chii_option_clusters(detections)
        self.chii_option_header_frames += 1
        if clusters:
            self.chii_option_history.append(clusters)
        else:
            self.statusBar().showMessage("Auto: seletor de Chii visivel; aguardando tiles das opcoes.")
            self.log_debug("Auto: seletor de Chii sem clusters detectados na area configurada.")
            return

        if self.chii_option_header_frames < 3 or len(self.chii_option_history) < 3:
            self.statusBar().showMessage(
                f"Auto: estabilizando opcoes de Chii ({self.chii_option_header_frames}/3)."
            )
            return

        stable_clusters = self.stable_chii_option_clusters()
        best = self.best_chii_option_cluster(state, stable_clusters)
        if best is None:
            self.log_debug("Auto: opcoes de Chii estaveis, mas nenhuma combina com as opcoes calculadas.")
            return

        if self.click_normalized_point(
            best.center_x,
            best.center_y,
            {"kind": "chii_option", "signature": best.signature},
        ):
            tiles = "+".join(tile.compact for tile in best.tiles)
            self.log_debug(f"Auto: agendou opcao de Chii {tiles}.")
            self.auto_last_click_at = time.perf_counter()
            self.reset_chii_option_tracker()

    def reset_chii_option_tracker(self) -> None:
        self.chii_option_history.clear()
        self.chii_option_header_frames = 0

    def start_auto_decision_log(self) -> None:
        if self.auto_decision_log_path is not None:
            return
        AUTO_LOG_DIR.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.auto_decision_log_path = AUTO_LOG_DIR / f"autoplay_{timestamp}.log"
        self.auto_decision_log_last_at = 0.0
        self.auto_decision_log_last_signature = ""
        self.auto_decision_log_events = 0
        self.auto_no_hand_frames = 0
        self.write_auto_decision_log(
            {
                "event": "session_start",
                "time": datetime.now().isoformat(timespec="seconds"),
                "logic_version": BOT_LOGIC_VERSION,
                "logic_notes": list(BOT_LOGIC_NOTES),
            }
        )
        self.statusBar().showMessage(f"Log AutoPlay iniciado: {self.auto_decision_log_path}")

    def finalize_auto_decision_log(self, reason: str) -> None:
        if self.auto_decision_log_path is None:
            return
        self.write_auto_decision_log(
            {
                "event": "session_end",
                "time": datetime.now().isoformat(timespec="seconds"),
                "reason": reason,
                "events": self.auto_decision_log_events,
            },
            force=True,
        )
        self.statusBar().showMessage(f"Log AutoPlay salvo: {self.auto_decision_log_path}")
        self.auto_decision_log_path = None
        self.auto_decision_log_last_signature = ""
        self.auto_decision_log_last_at = 0.0
        self.auto_decision_log_events = 0
        self.auto_no_hand_frames = 0
        self.auto_log_previous_hand_counter = Counter()

    def write_auto_decision_log(self, payload: dict, force: bool = False) -> None:
        if self.auto_decision_log_path is None:
            return
        payload.setdefault("time", datetime.now().isoformat(timespec="milliseconds"))
        payload.setdefault("logic_version", BOT_LOGIC_VERSION)
        try:
            with self.auto_decision_log_path.open("a", encoding="utf-8") as file:
                file.write(self.format_auto_decision_log_entry(payload) + "\n")
            if not force:
                self.auto_decision_log_events += 1
        except OSError as error:
            self.log_debug(f"Falha ao gravar log AutoPlay: {type(error).__name__}: {error}")

    def format_auto_decision_log_entry(self, payload: dict) -> str:
        event = str(payload.get("event", "event"))
        timestamp = str(payload.get("time", datetime.now().isoformat(timespec="milliseconds")))
        if event == "session_start":
            session_dt = datetime.fromisoformat(timestamp)
            notes = payload.get("logic_notes") or []
            dictionary = " ".join(f"{key}={self.sanitize_log_token(value)}" for key, value in AUTO_LOG_DICTIONARY.items())
            return "\n".join(
                [
                    "=" * 43,
                    f"Sessao {session_dt.strftime('%d-%m-%Y %H:%M')} version={payload.get('logic_version', BOT_LOGIC_VERSION)}",
                    f"DICT {dictionary}",
                    "NOTES=" + self.sanitize_log_token(";".join(str(note) for note in notes) if notes else "-"),
                    "=" * 43,
                ]
            )
        if event == "session_end":
            return (
                f"{self.auto_log_time_token(timestamp)} EV=SESSION_END "
                f"REASON={self.sanitize_log_token(payload.get('reason', '-'))} "
                f"EVENTS={payload.get('events', '-')}"
            )
        if event == "decision_frame":
            return self.format_compact_decision_line(payload)

        parts = [f"{self.auto_log_time_token(timestamp)} EV={self.sanitize_log_token(event.upper())}"]
        for key, value in payload.items():
            if key in {"event", "time", "logic_version"}:
                continue
            parts.append(f"{key.upper()}={self.sanitize_log_token(self.compact_log_value(value))}")
        return " ".join(parts)

    def format_compact_decision_line(self, payload: dict) -> str:
        progress = payload.get("hand_progress") or {}
        strategy = payload.get("strategy") or {}
        target = strategy.get("target") or {}
        scores = payload.get("score_ocr") or {}
        visible_actions = payload.get("visible_actions") or {}
        discards = payload.get("discards_by_player") or {}
        open_tiles = payload.get("opponent_open_tiles") or {}
        return " ".join(
            [
                f"{self.auto_log_time_token(str(payload.get('time', '')))}",
                "EV=FRAME",
                f"MP={self.join_log_values(payload.get('hand') or [])}",
                f"MA={self.join_log_values(payload.get('open_melds') or [], transform=self.meld_log_code_from_compact)}",
                f"PC={self.sanitize_log_token(payload.get('drawn_tile') or '-')}",
                f"DP={self.join_log_values(discards.get('principal') or [])}",
                f"DE={self.join_log_values(discards.get('esquerda') or [])}",
                f"DC={self.join_log_values(discards.get('cima') or [])}",
                f"DD={self.join_log_values(discards.get('direita') or [])}",
                f"AE={self.join_log_values(open_tiles.get('esquerda') or [])}",
                f"AC={self.join_log_values(open_tiles.get('cima') or [])}",
                f"AD={self.join_log_values(open_tiles.get('direita') or [])}",
                f"SC={self.score_log_code(scores)}",
                f"VE={self.player_winds_log_code(payload.get('player_winds') or {})}",
                f"DR={self.join_log_values(payload.get('dora_tiles') or [])}",
                f"DI={self.join_log_values(payload.get('dora_indicators') or [])}",
                f"ACT={self.actions_log_code(visible_actions)}",
                f"RI={self.join_log_values(payload.get('riichi_players') or [], transform=self.player_log_code)}",
                f"SH={progress.get('shanten', '-')}",
                f"PG={progress.get('percent', '-')}",
                f"ST={self.sanitize_log_token(strategy.get('mode') or '-')}",
                f"RK={strategy.get('risk_pressure', '-')}",
                f"OBJ={self.sanitize_log_token(target.get('key') or target.get('name') or '-')}",
                f"DS={self.join_log_values(payload.get('discard_candidates') or [])}",
                f"FW={self.join_log_values([item.get('tile') for item in payload.get('current_furiten_waits') or [] if item.get('tile')])}",
                f"FA={self.join_log_values([item.get('tile') for item in payload.get('furiten_waits_after_discards') or [] if item.get('tile')])}",
                f"DT={self.detections_log_code(payload.get('detections') or [])}",
            ]
        )

    @staticmethod
    def auto_log_time_token(timestamp: str) -> str:
        try:
            return datetime.fromisoformat(timestamp).strftime("%H:%M:%S")
        except ValueError:
            return datetime.now().strftime("%H:%M:%S")

    @staticmethod
    def sanitize_log_token(value) -> str:
        text = str(value if value is not None else "-")
        text = text.replace(" ", "_").replace("\n", "_").replace("\r", "_")
        return text or "-"

    def join_log_values(self, values, transform=None) -> str:
        transform = transform or self.tile_log_code
        items = [self.sanitize_log_token(transform(value)) for value in values if value is not None]
        return ",".join(item for item in items if item and item != "-") or "-"

    @staticmethod
    def tile_log_code(value) -> str:
        if isinstance(value, Tile):
            if value.suit == "man":
                return f"m{value.value}{'r' if value.red else ''}"
            if value.suit == "pin":
                return f"p{value.value}{'r' if value.red else ''}"
            if value.suit == "sou":
                return f"s{value.value}{'r' if value.red else ''}"
            if value.suit == "dragon":
                return {"white": "yd", "green": "gd", "red": "rd"}.get(str(value.value), f"d{value.value}")
            if value.suit == "wind":
                return {"east": "ew", "south": "sw", "west": "ww", "north": "nw"}.get(str(value.value), f"w{value.value}")
        text = str(value)
        for suffix, prefix in (("-man", "m"), ("-pin", "p"), ("-sou", "s")):
            if text.endswith(suffix) or text.endswith(suffix + "r"):
                red = "r" if text.endswith(suffix + "r") else ""
                number = text.split("-", 1)[0]
                return f"{prefix}{number}{red}"
        dragon_codes = {"white-dragon": "yd", "green-dragon": "gd", "red-dragon": "rd"}
        wind_codes = {"east-wind": "ew", "south-wind": "sw", "west-wind": "ww", "north-wind": "nw"}
        return dragon_codes.get(text, wind_codes.get(text, text.replace("-", "_")))

    def meld_log_code_from_compact(self, value) -> str:
        text = str(value)
        if " " not in text:
            return self.tile_log_code(text)
        kind, tiles_text = text.split(" ", 1)
        tiles_text = tiles_text.replace(" 4x", "")
        tiles = [self.tile_log_code(part) for part in tiles_text.split(",") if part]
        return f"{kind.lower()}({','.join(tiles)})"

    @staticmethod
    def player_log_code(value) -> str:
        return {"principal": "P", "esquerda": "E", "cima": "C", "direita": "D"}.get(str(value), str(value))

    def score_log_code(self, scores: dict) -> str:
        parts = []
        for player, label in (("principal", "P"), ("esquerda", "E"), ("cima", "C"), ("direita", "D")):
            score = scores.get(player) or {}
            value = score.get("value")
            parts.append(f"{label}:{value if value is not None else 'null'}")
        return ",".join(parts)

    def player_winds_log_code(self, winds: dict) -> str:
        parts = []
        for player, label in (("principal", "P"), ("esquerda", "E"), ("cima", "C"), ("direita", "D")):
            parts.append(f"{label}:{self.sanitize_log_token(winds.get(player, '?'))}")
        return ",".join(parts)

    @staticmethod
    def actions_log_code(actions: dict) -> str:
        mapping = (("Chii", "ch"), ("Pon", "pn"), ("Kan", "kn"), ("Riichi", "ri"), ("ChiiChoose", "cc"))
        return ",".join(f"{short}:{1 if actions.get(name) else 0}" for name, short in mapping)

    def detections_log_code(self, detections: list[dict]) -> str:
        items = []
        for detection in detections[:80]:
            name = self.tile_log_code(detection.get("name", ""))
            confidence = int(round(float(detection.get("confidence", 0.0)) * 100))
            cx = int(round(float(detection.get("cx", 0.0))))
            cy = int(round(float(detection.get("cy", 0.0))))
            items.append(f"{name}:{confidence}:{cx}:{cy}")
        return ";".join(items) or "-"

    def format_decision_frame_lines(self, payload: dict) -> list[str]:
        progress = payload.get("hand_progress") or {}
        value = payload.get("estimated_value") or {}
        strategy = payload.get("strategy") or {}
        target = strategy.get("target") or {}
        shape = payload.get("hand_shape") or {}
        lines = [
            f"HAND: {' | '.join(payload.get('hand') or []) or '-'}",
            f"OPEN: {' | '.join(payload.get('open_melds') or []) or '-'}",
            f"MISSING: {payload.get('missing_count', 0)}",
            f"WINDS: {self.compact_log_value(payload.get('player_winds') or {})}",
            f"ACTIONS: {self.compact_log_value(payload.get('visible_actions') or {})}",
            f"SCORE_OCR: {self.compact_log_value(payload.get('score_ocr') or {})}",
            (
                "PROGRESS: "
                f"shanten={progress.get('shanten')} percent={progress.get('percent')} label={progress.get('label')}"
            ),
            (
                "VALUE: "
                f"han={value.get('han')} fu={value.get('fu')} ron={value.get('ron_points')} "
                f"tsumo={value.get('tsumo_points')} yakus={self.compact_log_value(value.get('yakus') or [])} "
                f"bonus={self.compact_log_value(value.get('bonus_items') or [])}"
            ),
            (
                "STRATEGY: "
                f"mode={strategy.get('mode')} risk={strategy.get('risk_pressure')} "
                f"target={target.get('name') or '-'} prob={target.get('probability', '-')} "
                f"han={target.get('han', '-')} score={target.get('score', '-')} reason={strategy.get('reason')}"
            ),
            f"SHAPE: score={shape.get('score')} {shape.get('summary', '-')}",
            "ATTACK_CANDIDATES:",
        ]
        for objective in (strategy.get("candidates") or [])[:8]:
            lines.append(
                f"  - {objective.get('name')} prob={objective.get('probability')} "
                f"han={objective.get('han')} score={objective.get('score')} wanted={objective.get('wanted')}"
            )
        lines.append("CALL_DECISIONS:")
        for decision in payload.get("call_decisions") or []:
            lines.append(
                f"  - {decision.get('action')} rec={decision.get('recommended')} "
                f"conf={decision.get('confidence')} option={decision.get('option')} reason={decision.get('reason')}"
            )
        lines.append("YAKU:")
        for yaku in payload.get("yaku") or []:
            lines.append(
                f"  - {yaku.get('name')} {yaku.get('confidence')}% reason={yaku.get('reason')}"
            )
        lines.append("HELPFUL_TILES:")
        for need in payload.get("helpful_tiles") or []:
            lines.append(f"  - {need.get('tile')} score={need.get('score')} reason={need.get('reason')}")
        lines.extend(
            [
                f"DORA_INDICATORS: {' | '.join(payload.get('dora_indicators') or []) or '-'}",
                f"DORA_TILES: {' | '.join(payload.get('dora_tiles') or []) or '-'}",
                f"DISCARDS_BY_PLAYER: {self.compact_log_value(payload.get('discards_by_player') or {})}",
                f"OPPONENT_OPEN_TILES: {self.compact_log_value(payload.get('opponent_open_tiles') or {})}",
                f"RIICHI_PLAYERS: {' | '.join(payload.get('riichi_players') or []) or '-'}",
                f"RIICHI_MARKS: {self.compact_log_value(payload.get('riichi_discard_marks') or {})}",
                f"RIICHI_BEFORE_AFTER: {self.compact_log_value(payload.get('riichi_discards_before_after') or {})}",
                f"OWN_DISCARDS: {' | '.join(payload.get('own_discards') or []) or '-'}",
                f"CURRENT_FURITEN: {self.compact_log_value(payload.get('current_furiten_waits') or [])}",
                f"FURITEN_AFTER_DISCARDS: {self.compact_log_value(payload.get('furiten_waits_after_discards') or [])}",
                f"RIICHI_DISCARD_CANDIDATES: {' | '.join(payload.get('riichi_discard_candidates') or []) or '-'}",
                f"DISCARD_CANDIDATES: {' | '.join(payload.get('discard_candidates') or []) or '-'}",
                "DISCARD_EXPLANATIONS:",
            ]
        )
        for explanation in payload.get("discard_explanations") or []:
            lines.append(
                f"  #{explanation.get('rank')} {explanation.get('tile')} color={explanation.get('color')} "
                f"risk={explanation.get('pressure')} visible={explanation.get('visible_count')}/4 "
                f"remaining={explanation.get('remaining_count')} shanten_after={explanation.get('shanten_after')} "
                f"ukeire={explanation.get('ukeire_after')}"
            )
            for reason in explanation.get("reasons") or []:
                lines.append(f"      reason: {reason}")
            for safeguard in explanation.get("safeguards") or []:
                lines.append(f"      safeguard: {safeguard}")
        lines.extend(
            [
                f"PON_CONTEXT: {self.compact_log_value(payload.get('pon_context') or {})}",
                f"CHII_CONTEXT: {self.compact_log_value(payload.get('chii_context') or {})}",
                f"KAN_OPTIONS: {self.compact_log_value(payload.get('kan_options') or [])}",
                f"PIXEL_TRIGGERS_POSITIVE: {self.compact_log_value(payload.get('pixel_triggers') or [])}",
                f"PIXEL_TRIGGERS_ALL: {self.compact_log_value(payload.get('pixel_triggers_all') or [])}",
                f"ROUTINE_STATE: {self.compact_log_value(payload.get('routine_state') or {})}",
                "DETECTIONS:",
            ]
        )
        for detection in payload.get("detections") or []:
            lines.append(
                f"  - {detection.get('name')} conf={detection.get('confidence')} "
                f"box=({detection.get('x1')},{detection.get('y1')},{detection.get('x2')},{detection.get('y2')}) "
                f"center=({detection.get('cx')},{detection.get('cy')})"
            )
        return lines

    def compact_log_value(self, value) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    @staticmethod
    def detection_log_payload(detection) -> dict:
        return {
            "name": getattr(detection, "name", ""),
            "confidence": round(float(getattr(detection, "confidence", 0.0)), 3),
            "x1": round(float(getattr(detection, "x1", 0.0)), 1),
            "y1": round(float(getattr(detection, "y1", 0.0)), 1),
            "x2": round(float(getattr(detection, "x2", 0.0)), 1),
            "y2": round(float(getattr(detection, "y2", 0.0)), 1),
            "cx": round(float(getattr(detection, "center_x", 0.0)), 1),
            "cy": round(float(getattr(detection, "center_y", 0.0)), 1),
        }

    def log_auto_decision_frame(
        self,
        detections: list,
        state: HandState,
        riichi_button_visible: bool,
        win_button_visible: bool,
        chii_choose_visible: bool,
    ) -> None:
        if self.auto_decision_log_path is None:
            self.start_auto_decision_log()
        hand_count = len(state.hand_tiles)
        if hand_count < 5 and not (state.chii_button_visible or state.pon_button_visible or state.kan_button_visible):
            self.auto_no_hand_frames += 1
        else:
            self.auto_no_hand_frames = 0

        visible_actions = {
            "Chii": state.chii_button_visible,
            "Pon": state.pon_button_visible,
            "Kan": state.kan_button_visible,
            "Riichi": riichi_button_visible,
            "ChiiChoose": chii_choose_visible,
        }
        inverse_probe_statuses = self.pixel_trigger_statuses(self.latest_auto_image or self.last_capture)
        progress = hand_completion_progress(state)
        shape = hand_shape_profile(state.hand_tiles, state.open_melds)
        value = estimated_hand_value(state)
        decisions = [decision.compact for decision in state.call_decisions]
        now = time.perf_counter()
        if now - self.auto_decision_log_last_at < 1.0:
            return
        self.auto_decision_log_last_at = now
        drawn_tile = self.infer_drawn_tile_for_log(state.hand_tiles)
        self.write_auto_decision_log(
            {
                "event": "decision_frame",
                "logic_notes": list(BOT_LOGIC_NOTES),
                "detections": [self.detection_log_payload(detection) for detection in detections],
                "hand": [tile.compact for tile in state.hand_tiles],
                "open_melds": [meld.compact for meld in state.open_melds],
                "drawn_tile": drawn_tile,
                "missing_count": state.missing_count,
                "player_winds": state.player_winds,
                "visible_actions": visible_actions,
                "score_ocr": self.latest_score_ocr,
                "hand_progress": {
                    "shanten": progress.shanten,
                    "percent": progress.percent,
                    "label": progress.label,
                },
                "hand_shape": {
                    "score": shape.score,
                    "blocks": shape.blocks,
                    "complete_blocks": shape.complete_blocks,
                    "ryanmen": shape.ryanmen,
                    "ryankan": shape.ryankan,
                    "kanchan": shape.kanchan,
                    "penchan": shape.penchan,
                    "pairs": shape.pairs,
                    "excess_pairs": shape.excess_pairs,
                    "isolated_middle": shape.isolated_middle,
                    "isolated_terminal": shape.isolated_terminal,
                    "isolated_honor": shape.isolated_honor,
                    "summary": shape.summary,
                },
                "estimated_value": {
                    "han": value.han,
                    "fu": value.fu,
                    "yakus": [{"name": name, "han": han} for name, han in value.yakus],
                    "bonus_han": value.bonus_han,
                    "bonus_items": [{"name": name, "han": han} for name, han in value.bonus_items],
                    "ron_points": value.ron_points,
                    "tsumo_points": value.tsumo_points,
                    "limit_name": value.limit_name,
                    "dealer": value.dealer,
                    "note": value.note,
                },
                "strategy": {
                    "mode": state.strategy_mode,
                    "reason": state.strategy_reason,
                    "risk_pressure": table_risk_pressure(state),
                    "target": (
                        {
                            "key": state.attack_objective.yaku_key,
                            "name": state.attack_objective.name,
                            "probability": state.attack_objective.probability,
                            "han": state.attack_objective.han,
                            "score": state.attack_objective.score,
                            "reason": state.attack_objective.reason,
                            "wanted": list(state.attack_objective.wanted),
                        }
                        if state.attack_objective is not None
                        else None
                    ),
                    "candidates": [
                        {
                            "key": objective.yaku_key,
                            "name": objective.name,
                            "probability": objective.probability,
                            "han": objective.han,
                            "score": objective.score,
                            "reason": objective.reason,
                            "wanted": list(objective.wanted),
                        }
                        for objective in state.attack_candidates[:6]
                    ],
                },
                "pixel_triggers": [
                    status for status in inverse_probe_statuses if status.get("positive")
                ],
                "pixel_triggers_all": inverse_probe_statuses,
                "routine_state": self.routine_state_snapshot(),
                "call_decisions": [
                    {
                        "action": decision.action,
                        "recommended": decision.recommended,
                        "confidence": decision.confidence,
                        "option": decision.option,
                        "reason": decision.reason,
                    }
                    for decision in state.call_decisions
                ],
                "yaku": [
                    {"key": match.yaku.key, "name": match.yaku.name, "confidence": match.confidence, "reason": match.reason}
                    for match in state.likely_yaku
                ],
                "helpful_tiles": [
                    {"tile": need.tile.compact, "score": need.score, "reason": need.reason}
                    for need in state.helpful_missing_tiles[:8]
                ],
                "dora_indicators": [tile.compact for tile in state.dora_indicators],
                "dora_tiles": [tile.compact for tile in state.dora_tiles],
                "discards_by_player": {
                    player: [tile.compact for tile in tiles]
                    for player, tiles in state.discarded_by_player.items()
                },
                "riichi_players": list(state.riichi_players),
                "riichi_discard_marks": dict(state.riichi_discard_marks),
                "riichi_discards_before_after": {
                    player: {
                        "before": [
                            tile.compact
                            for tile in state.discarded_by_player.get(player, [])[: state.riichi_discard_marks.get(player, 0)]
                        ],
                        "after": [
                            tile.compact
                            for tile in state.discarded_by_player.get(player, [])[state.riichi_discard_marks.get(player, 0) :]
                        ],
                    }
                    for player in state.riichi_players
                },
                "opponent_open_tiles": {
                    player: [tile.compact for tile in tiles]
                    for player, tiles in state.opponent_open_tiles.items()
                },
                "own_discards": [tile.compact for tile in state.discarded_by_player.get("principal", [])],
                "current_furiten_waits": [
                    {"tile": need.tile.compact, "reason": need.reason}
                    for need in state.current_furiten_waits
                ],
                "riichi_discard_candidates": [
                    tile.compact for tile in riichi_discard_candidates(state)
                ],
                "furiten_waits_after_discards": [
                    {"tile": need.tile.compact, "reason": need.reason}
                    for need in state.furiten_waits
                ],
                "discard_candidates": [tile.compact for tile in state.discard_candidates[:4]],
                "discard_explanations": [
                    {
                        "tile": explanation.tile.compact,
                        "rank": explanation.rank,
                        "color": explanation.color,
                        "pressure": explanation.pressure,
                        "reasons": list(explanation.reasons),
                        "safeguards": list(explanation.safeguards),
                        "visible_count": explanation.visible_count,
                        "remaining_count": explanation.remaining_count,
                        "shanten_after": explanation.shanten_after,
                        "ukeire_after": explanation.ukeire_after,
                    }
                    for explanation in state.discard_explanations
                ],
                "pon_context": {
                    "source_player": state.pon_source_player,
                    "discard": state.pon_discard.compact if state.pon_discard else None,
                    "options": [option.compact for option in state.pon_options],
                },
                "chii_context": {
                    "discard": state.chii_discard.compact if state.chii_discard else None,
                    "options": [option.compact for option in state.chii_options],
                },
                "kan_options": [option.compact for option in state.kan_options],
            }
        )

    def infer_drawn_tile_for_log(self, hand_tiles: list[Tile]) -> str:
        current_counter = Counter(tile.key for tile in hand_tiles)
        added = current_counter - self.auto_log_previous_hand_counter
        self.auto_log_previous_hand_counter = current_counter
        if not added or sum(added.values()) != 1:
            return "-"
        key = next(iter(added))
        for tile in hand_tiles:
            if tile.key == key:
                return self.tile_log_code(tile)
        return self.tile_log_code(key.replace("_", "-"))

    def detect_chii_option_clusters(self, detections: list) -> list[ChiiOptionCluster]:
        region = self.configured_region_rect("chii_options")
        candidates = []
        for detection in detections:
            tile = tile_from_name(detection.name)
            if tile is None:
                continue
            if not region.contains(QPointF(detection.center_x, detection.center_y)):
                continue
            if detection.width <= 8 or detection.height <= 12:
                continue
            candidates.append((detection, tile))
        if len(candidates) < 2:
            return []

        candidates = self.deduplicate_chii_option_detections(candidates)
        candidates.sort(key=lambda item: item[0].center_x)
        clusters = []
        for index in range(0, len(candidates) - 1, 2):
            pair = candidates[index : index + 2]
            if len(pair) != 2:
                continue
            detections_pair = [item[0] for item in pair]
            tiles = tuple(item[1] for item in pair)
            signature = tuple(sorted(tile.base_key for tile in tiles))
            center_x = sum(item.center_x for item in detections_pair) / 2
            center_y = sum(item.center_y for item in detections_pair) / 2
            confidence = sum(item.confidence for item in detections_pair) / 2
            clusters.append(ChiiOptionCluster(tiles, center_x, center_y, signature, confidence))
        return clusters

    @staticmethod
    def deduplicate_chii_option_detections(candidates: list[tuple[object, Tile]]) -> list[tuple[object, Tile]]:
        accepted: list[tuple[object, Tile]] = []
        for detection, tile in sorted(candidates, key=lambda item: item[0].confidence, reverse=True):
            duplicate = False
            for existing, existing_tile in accepted:
                if existing_tile.base_key != tile.base_key:
                    continue
                if abs(existing.center_x - detection.center_x) <= max(existing.width, detection.width) * 0.35:
                    duplicate = True
                    break
            if not duplicate:
                accepted.append((detection, tile))
        return accepted

    def stable_chii_option_clusters(self) -> list[ChiiOptionCluster]:
        signature_counts = Counter(
            cluster.signature
            for frame in self.chii_option_history
            for cluster in frame
        )
        latest = self.chii_option_history[-1] if self.chii_option_history else []
        stable = [cluster for cluster in latest if signature_counts[cluster.signature] >= 2]
        return stable or latest

    def best_chii_option_cluster(self, state: HandState, clusters: list[ChiiOptionCluster]) -> ChiiOptionCluster | None:
        best_cluster = None
        best_score = -1
        for cluster in clusters:
            option = self.match_chii_option_for_cluster(state, cluster)
            if option is None:
                continue
            simulated = simulate_call("Chii", state, option)
            if simulated is None:
                continue
            simulated.likely_yaku = likely_yaku(simulated)
            score = call_plan_score_from_plans(call_candidate_plans(simulated))
            score += round(cluster.confidence * 4)
            if score > best_score:
                best_score = score
                best_cluster = cluster
        return best_cluster

    @staticmethod
    def match_chii_option_for_cluster(state: HandState, cluster: ChiiOptionCluster):
        cluster_counts = Counter(cluster.signature)
        for option in state.chii_options:
            option_counts = Counter(tile.base_key for tile in option.needed_tiles)
            if option_counts == cluster_counts:
                return option
        return None

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

    def click_win_button_if_visible(self, ron_visible: bool, tsumo_visible: bool) -> bool:
        action = "Tsumo" if tsumo_visible else "Ron" if ron_visible else ""
        if not action:
            return False
        if self.auto_click_pending:
            pending_kind = (self.auto_pending_context or {}).get("kind")
            pending_action = (self.auto_pending_context or {}).get("action")
            if pending_kind == "action" and pending_action in {"Ron", "Tsumo"}:
                return True
            self.cancel_pending_auto_click("vitoria detectada")
        if self.click_action_button(action):
            self.log_debug(f"Auto: {action} detectado; clique prioritario agendado.")
            self.auto_last_click_at = time.perf_counter()
            self.auto_turn_frames = 0
            return True
        self.statusBar().showMessage(f"Auto: {action} detectado, mas pixel do botao nao esta ativo/configurado.")
        return False

    def cancel_pending_auto_click(self, reason: str) -> None:
        if not self.auto_click_pending:
            return
        self.auto_click_generation += 1
        self.auto_click_pending = False
        self.auto_pending_context = None
        self.log_debug(f"Auto: clique pendente cancelado: {reason}.")

    def click_action_button(self, action: str) -> bool:
        probe = self.active_button_probe(self.latest_auto_image or self.last_capture, action)
        if probe is None:
            return False
        return self.click_normalized_point(
            float(probe["x"]),
            float(probe["y"]),
            {"kind": "action", "action": action},
        )

    def click_skip_button(self, skipped_actions: list[str]) -> bool:
        probe = self.skip_button_probe()
        if probe is None:
            self.log_debug("Auto: Skip indicado, mas pixel de Skip nao esta configurado.")
            return False
        return self.click_normalized_point(
            float(probe["x"]),
            float(probe["y"]),
            {"kind": "skip", "skipped_actions": tuple(skipped_actions)},
        )

    def skip_button_probe(self) -> dict | None:
        active_probe = self.active_button_probe(self.latest_auto_image or self.last_capture, "Skip")
        if active_probe is not None:
            return active_probe
        probes = self.app_config.get("pixel_probes", {})
        for key in self.button_probe_keys("Skip"):
            probe = probes.get(key)
            if probe is not None:
                return probe
        return None

    def click_best_discard(self, detections: list, state: HandState) -> bool:
        discard_tile = self.next_auto_discard_candidate(state)
        if discard_tile is None:
            return False
        return self.click_discard_tile(detections, state, discard_tile, "discard")

    def click_riichi_discard(self, detections: list, state: HandState) -> bool:
        candidates = riichi_discard_candidates(state)
        if not candidates:
            return False
        return self.click_discard_tile(detections, state, candidates[0], "riichi_discard")

    def click_discard_tile(self, detections: list, state: HandState, discard_tile: Tile, kind: str) -> bool:
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
            for detection in detections:
                tile = tile_from_name(detection.name)
                if tile is None or tile.base_key != target_key:
                    continue
                if player_region.contains(QPointF(detection.center_x, detection.center_y)):
                    candidates.append(detection)
        if kind == "riichi_discard" and not candidates:
            for detection in detections:
                tile = tile_from_name(detection.name)
                if tile is None or tile.base_key != target_key:
                    continue
                if self.detection_in_riichi_choice_region(detection):
                    candidates.append(detection)
        if not candidates:
            self.log_debug(f"Auto: nao encontrou box clicavel para {discard_tile.compact}.")
            return False

        target = self.best_discard_click_detection(candidates, discard_tile)
        return self.click_normalized_point(
            target.center_x,
            target.center_y,
            {"kind": kind, "tile_key": target_key, "tile_label": discard_tile.compact},
        )

    @staticmethod
    def best_discard_click_detection(candidates: list, discard_tile: Tile):
        if discard_tile.is_suited and int(discard_tile.value) == 5 and not discard_tile.red:
            normal_candidates = [
                detection
                for detection in candidates
                if (tile := tile_from_name(detection.name)) is not None and not tile.red
            ]
            if normal_candidates:
                return max(normal_candidates, key=lambda item: item.center_y)
        return max(candidates, key=lambda item: item.center_y)

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

    def detection_in_riichi_choice_region(self, detection) -> bool:
        point = QPointF(detection.center_x, detection.center_y)
        return any(region.contains(point) for region in self.riichi_choice_regions())

    def riichi_choice_regions(self) -> list[QRectF]:
        regions: list[QRectF] = []
        if "chii_options" in self.app_config.get("regions", {}):
            configured = self.configured_region_rect("chii_options")
            if not configured.isNull():
                regions.append(configured)
        regions.append(
            QRectF(
                CAPTURE_WIDTH * 0.32,
                CAPTURE_HEIGHT * 0.42,
                CAPTURE_WIDTH * 0.42,
                CAPTURE_HEIGHT * 0.30,
            )
        )
        return regions

    def click_normalized_point(self, x: float, y: float, context: dict | None = None) -> bool:
        if self.auto_click_pending:
            return False
        screen_point = self.normalized_point_to_screen(x, y)
        if screen_point is None:
            return False
        hwnd, screen_x, screen_y = screen_point

        move_delay_seconds = random.uniform(*self.auto_mouse_delay_seconds)
        move_delay_ms = round(move_delay_seconds * 1000)
        self.auto_click_pending = True
        self.auto_pending_context = context
        self.auto_click_generation += 1
        generation = self.auto_click_generation
        self.statusBar().showMessage(f"Auto: vai mover o mouse em {move_delay_seconds:.1f}s.")
        QTimer.singleShot(
            move_delay_ms,
            lambda: self.finish_scheduled_mouse_move(hwnd, screen_x, screen_y, generation),
        )
        return True

    def normalized_point_to_screen(self, x: float, y: float) -> tuple[int, int, int] | None:
        hwnd = self.selected_hwnd()
        if hwnd is None:
            return None
        bounds = window_bounds(hwnd)
        if bounds is None:
            return None
        monitor = clamp_to_monitor(bounds, self.screen_capture.monitors)
        if monitor is None:
            return None

        scale = max(CAPTURE_WIDTH / monitor["width"], CAPTURE_HEIGHT / monitor["height"])
        scaled_width = monitor["width"] * scale
        scaled_height = monitor["height"] * scale
        crop_x = max(0.0, (scaled_width - CAPTURE_WIDTH) / 2)
        crop_y = max(0.0, (scaled_height - CAPTURE_HEIGHT) / 2)
        screen_x = int(monitor["left"] + (x + crop_x) / scale)
        screen_y = int(monitor["top"] + (y + crop_y) / scale)
        return hwnd, screen_x, screen_y

    def handle_routines(self, image: QImage) -> None:
        if not self.routines_checkbox.isChecked() or self.routine_click_pending:
            return
        rules = self.app_config.get("routine_rules", {})
        if rules:
            self.handle_routine_rules(image)
            return
        sequences = self.app_config.get("routine_sequences", {})
        for routine_key, sequence in sequences.items():
            if not sequence.get("enabled", True):
                continue
            steps = [step for step in sequence.get("steps", []) if isinstance(step, dict) and step.get("enabled", True)]
            if not steps:
                continue
            index = min(self.routine_step_index.get(routine_key, 0), len(steps) - 1)
            step = steps[index]
            status = self.routine_step_status(image, step)
            ready_key = f"{routine_key}:{index}"
            if not status or not status.get("positive"):
                self.routine_step_ready_since.pop(ready_key, None)
                if index:
                    self.statusBar().showMessage(
                        f"Rotina {routine_key}: aguardando etapa {index + 1} ({step.get('detect', '-')})."
                    )
                continue

            now = time.perf_counter()
            cooldown = float(sequence.get("cooldown_seconds", 0.8) or 0.0)
            if now - self.routine_last_click_at.get(routine_key, 0.0) < cooldown:
                continue

            delay_seconds = max(0.0, float(step.get("delay_seconds", 0.0) or 0.0))
            ready_since = self.routine_step_ready_since.setdefault(ready_key, now)
            elapsed = now - ready_since
            if elapsed < delay_seconds:
                remaining = delay_seconds - elapsed
                self.statusBar().showMessage(
                    f"Rotina {routine_key}: etapa {index + 1} detectada; clique em {remaining:.1f}s."
                )
                continue

            target = self.routine_click_target(step, status)
            if target is None:
                self.log_debug(f"Rotina {routine_key}: alvo de clique invalido na etapa {index + 1}.")
                self.routine_step_ready_since.pop(ready_key, None)
                continue
            if self.click_routine_target(target, routine_key, index, step, status):
                self.routine_step_ready_since.pop(ready_key, None)
                return

    def handle_routine_rules(self, image: QImage) -> None:
        ready_rules: list[tuple[float, float, str, dict, dict]] = []
        waiting: list[tuple[float, str]] = []
        now = time.perf_counter()
        for rule_key, rule in self.app_config.get("routine_rules", {}).items():
            if not rule.get("enabled", True):
                self.routine_step_ready_since.pop(f"rule:{rule_key}", None)
                continue
            status = self.routine_rule_status(image, rule_key)
            ready_key = f"rule:{rule_key}"
            if not status or not status.get("positive"):
                self.routine_step_ready_since.pop(ready_key, None)
                continue
            cooldown = 0.35
            if now - self.routine_last_click_at.get(ready_key, 0.0) < cooldown:
                continue
            delay_seconds = max(0.0, float(rule.get("delay_seconds", 1.0) or 0.0))
            ready_since = self.routine_step_ready_since.setdefault(ready_key, now)
            elapsed = now - ready_since
            remaining = delay_seconds - elapsed
            if remaining <= 0:
                ready_rules.append((delay_seconds, ready_since, rule_key, rule, status))
            else:
                waiting.append((remaining, str(rule.get("label", rule_key))))

        if ready_rules:
            _delay, _ready_since, rule_key, rule, status = sorted(ready_rules, key=lambda item: (item[0], item[1]))[0]
            if self.click_routine_rule(rule_key, rule, status):
                self.routine_step_ready_since.pop(f"rule:{rule_key}", None)
            return

        if waiting:
            remaining, label = sorted(waiting, key=lambda item: item[0])[0]
            self.statusBar().showMessage(f"Rotina: {label} detectado; clique em {remaining:.1f}s.")

    def routine_statuses(self, image: QImage | None) -> list[dict]:
        rows: list[dict] = []
        rules = self.app_config.get("routine_rules", {})
        if rules:
            for rule_key, rule in rules.items():
                if not rule.get("enabled", True):
                    continue
                status = self.routine_rule_status(image, rule_key)
                ready_key = f"rule:{rule_key}"
                ready_since = self.routine_step_ready_since.get(ready_key)
                delay_seconds = float(rule.get("delay_seconds", 1.0) or 0.0)
                elapsed = max(0.0, time.perf_counter() - ready_since) if ready_since else 0.0
                rows.append(
                    {
                        "routine": rule_key,
                        "step": 0,
                        "total": 1,
                        "role": "igual",
                        "key": rule_key,
                        "positive": bool(status and status.get("positive")),
                        "distance": status.get("distance") if status else "-",
                        "tolerance": status.get("tolerance") if status else "-",
                        "current_color": status.get("current_color") if status else "sem pixel",
                        "repeat": round(min(elapsed, delay_seconds), 1),
                        "repeat_total": delay_seconds,
                    }
                )
            return rows
        sequences = self.app_config.get("routine_sequences", {})
        for routine_key, sequence in sequences.items():
            if not sequence.get("enabled", True):
                continue
            steps = [step for step in sequence.get("steps", []) if isinstance(step, dict) and step.get("enabled", True)]
            if not steps:
                continue
            index = min(self.routine_step_index.get(routine_key, 0), len(steps) - 1)
            step = steps[index]
            status = self.routine_step_status(image, step)
            probe_key = str(step.get("detect") or "")
            repeat_key = f"{routine_key}:{index}"
            if status is None:
                rows.append(
                    {
                        "routine": routine_key,
                        "step": index,
                        "total": len(steps),
                        "role": "detecta",
                        "key": probe_key,
                        "positive": False,
                        "distance": "-",
                        "tolerance": "-",
                        "current_color": "sem pixel",
                        "repeat": self.routine_step_repeat_count.get(repeat_key, 0),
                        "repeat_total": int(step.get("repeat", 1) or 1),
                    }
                )
                continue
            rows.append(
                {
                    "routine": routine_key,
                    "step": index,
                    "total": len(steps),
                    "role": "igual" if step.get("condition") != "not_match" else "diferente",
                    "key": probe_key,
                    "positive": bool(status.get("positive")),
                    "distance": status.get("distance"),
                    "tolerance": status.get("tolerance"),
                    "current_color": status.get("current_color"),
                    "repeat": self.routine_step_repeat_count.get(repeat_key, 0),
                    "repeat_total": int(step.get("repeat", 1) or 1),
                }
            )
        return rows

    def maybe_log_routine_statuses(self) -> None:
        now = time.perf_counter()
        if now - self.routine_status_log_last_at < 1.0:
            return
        self.routine_status_log_last_at = now
        if not self.latest_routine_statuses:
            self.log_debug("Rotina: sem status de pixel.")
            return
        parts = []
        for item in self.latest_routine_statuses[:6]:
            parts.append(
                f"{item.get('routine')}[{int(item.get('step', 0)) + 1}/{item.get('total')}] "
                f"{item.get('role')}={item.get('key')}:{'SIM' if item.get('positive') else 'nao'} "
                f"d{item.get('distance')}/t{item.get('tolerance')} cor={item.get('current_color')}"
            )
        self.log_debug("Rotina pixels: " + " | ".join(parts))

    def routine_rule(self, key: str) -> dict | None:
        rule = self.app_config.get("routine_rules", {}).get(key)
        if not rule or not rule.get("enabled", True):
            return None
        return rule

    def routine_rule_status(self, image: QImage | None, key: str) -> dict | None:
        rule = self.routine_rule(key)
        if image is None or image.isNull() or rule is None:
            return None
        x = int(rule.get("x", 0))
        y = int(rule.get("y", 0))
        if not (0 <= x < image.width() and 0 <= y < image.height()):
            return None
        current = image.pixelColor(x, y)
        expected = QColor(str(rule.get("color", "#000000")))
        tolerance = int(rule.get("tolerance", 45))
        distance = self.color_distance(current, expected)
        return {
            "key": key,
            "label": rule.get("label", key),
            "condition": "match",
            "positive": distance <= tolerance,
            "distance": distance,
            "tolerance": tolerance,
            "current_color": current.name(),
            "expected_color": expected.name(),
            "x": x,
            "y": y,
        }

    def click_routine_rule(self, rule_key: str, rule: dict, status: dict) -> bool:
        screen_point = self.normalized_point_to_screen(float(rule["x"]), float(rule["y"]))
        if screen_point is None:
            return False
        hwnd, screen_x, screen_y = screen_point
        self.routine_click_pending = True
        label = str(rule.get("label", rule_key))
        self.statusBar().showMessage(f"Rotina: clicando {label}.")
        self.write_auto_decision_log(
            {
                "event": "routine_click_scheduled",
                "routine": rule_key,
                "step": 0,
                "step_label": label,
                "detect": rule_key,
                "condition": "match",
                "target": {"x": rule.get("x"), "y": rule.get("y"), "label": label, "probe": rule_key},
                "status": status,
                "label": label,
                "delay_seconds": rule.get("delay_seconds", 1.0),
            }
        )
        QTimer.singleShot(80, lambda: self.finish_routine_rule_click(hwnd, screen_x, screen_y, rule_key))
        return True

    def finish_routine_rule_click(self, hwnd: int, screen_x: int, screen_y: int, rule_key: str) -> None:
        try:
            if not self.routines_checkbox.isChecked():
                return
            status = self.routine_rule_status(self.last_capture, rule_key)
            if not status or not status.get("positive"):
                self.statusBar().showMessage("Rotina: clique cancelado; pixel mudou.")
                self.write_auto_decision_log(
                    {
                        "event": "routine_click_cancelled",
                        "routine": rule_key,
                        "step": 0,
                        "reason": "condition_changed_before_click",
                        "status": status,
                    }
                )
                return
            user32.SetForegroundWindow(hwnd)
            user32.SetCursorPos(screen_x, screen_y)
            user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
            user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
            ready_key = f"rule:{rule_key}"
            self.routine_last_click_at[ready_key] = time.perf_counter()
            self.routine_step_ready_since.pop(ready_key, None)
            self.statusBar().showMessage(f"Rotina: clique executado em {status.get('label', rule_key)}.")
            self.write_auto_decision_log(
                {
                    "event": "routine_click",
                    "routine": rule_key,
                    "step": 0,
                    "step_label": status.get("label", rule_key),
                    "state": self.routine_state_snapshot(),
                }
            )
        finally:
            self.routine_click_pending = False

    def routine_probe(self, key: str) -> dict | None:
        probe = self.app_config.get("routine_probes", {}).get(key)
        if not probe or not probe.get("enabled", True):
            return None
        return probe

    def routine_probe_status(
        self,
        image: QImage | None,
        key: str,
        condition: str = "match",
    ) -> dict | None:
        probe = self.routine_probe(key)
        if image is None or image.isNull() or probe is None:
            return None
        x = int(probe.get("x", 0))
        y = int(probe.get("y", 0))
        if not (0 <= x < image.width() and 0 <= y < image.height()):
            return None
        current = image.pixelColor(x, y)
        expected = QColor(str(probe.get("color", "#000000")))
        tolerance = int(probe.get("tolerance", 45))
        distance = self.color_distance(current, expected)
        matched_color = distance <= tolerance
        positive = not matched_color if condition == "not_match" else matched_color
        return {
            "key": key,
            "label": probe.get("label", key),
            "condition": condition,
            "positive": positive,
            "distance": distance,
            "tolerance": tolerance,
            "current_color": current.name(),
            "expected_color": expected.name(),
            "x": x,
            "y": y,
        }

    def routine_step_status(self, image: QImage | None, step: dict) -> dict | None:
        detect_key = str(step.get("detect") or "")
        if not detect_key:
            return {"positive": True, "x": int(step.get("x", 0)), "y": int(step.get("y", 0)), "key": ""}
        condition = "not_match" if step.get("condition") == "not_match" else "match"
        return self.routine_probe_status(image, detect_key, condition)

    def routine_click_target(self, step: dict, status: dict) -> dict | None:
        click_mode = str(step.get("click_mode", "detect"))
        if click_mode == "point":
            return {
                "x": int(step.get("x", 0)),
                "y": int(step.get("y", 0)),
                "label": "ponto fixo",
                "probe": "",
            }
        if click_mode == "probe":
            probe_key = str(step.get("click_probe") or "")
            probe = self.routine_probe(probe_key)
            if probe is None:
                return None
            return {
                "x": int(probe.get("x", 0)),
                "y": int(probe.get("y", 0)),
                "label": probe.get("label", probe_key),
                "probe": probe_key,
            }
        return {
            "x": int(status.get("x", 0)),
            "y": int(status.get("y", 0)),
            "label": status.get("label", status.get("key", "pixel detectado")),
            "probe": status.get("key", ""),
        }

    def click_routine_target(
        self,
        target: dict,
        routine_key: str,
        step_index: int,
        step: dict,
        status: dict,
    ) -> bool:
        screen_point = self.normalized_point_to_screen(float(target["x"]), float(target["y"]))
        if screen_point is None:
            return False
        hwnd, screen_x, screen_y = screen_point
        self.routine_click_pending = True
        label = str(target.get("label", "alvo"))
        self.statusBar().showMessage(f"Rotina {routine_key}: clicando {label}.")
        self.write_auto_decision_log(
            {
                "event": "routine_click_scheduled",
                "routine": routine_key,
                "step": step_index,
                "step_label": step.get("label", ""),
                "detect": step.get("detect", ""),
                "condition": step.get("condition", "match"),
                "target": target,
                "status": status,
                "label": label,
                "delay_seconds": 0.0,
            }
        )
        QTimer.singleShot(
            80,
            lambda: self.finish_routine_click(hwnd, screen_x, screen_y, routine_key, step_index),
        )
        return True

    def finish_routine_click(self, hwnd: int, screen_x: int, screen_y: int, routine_key: str, step_index: int) -> None:
        try:
            if not self.routines_checkbox.isChecked():
                return
            sequence, steps, step = self.current_routine_step(routine_key, step_index)
            if step is None:
                return
            status = self.routine_step_status(self.last_capture, step)
            if not status or not status.get("positive"):
                self.statusBar().showMessage("Rotina: clique cancelado; condicao da etapa mudou.")
                self.write_auto_decision_log(
                    {
                        "event": "routine_click_cancelled",
                        "routine": routine_key,
                        "step": step_index,
                        "step_label": step.get("label", ""),
                        "reason": "condition_changed_before_click",
                        "status": status,
                    }
                )
                return
            user32.SetForegroundWindow(hwnd)
            user32.SetCursorPos(screen_x, screen_y)
            user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
            user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
            self.statusBar().showMessage(f"Rotina {routine_key}: clique executado.")
            self.log_debug(f"Rotina {routine_key}: clique etapa {step_index + 1}.")
            self.advance_routine_after_click(routine_key, step_index, step, len(steps))
            self.routine_last_click_at[routine_key] = time.perf_counter()
            self.write_auto_decision_log(
                {
                    "event": "routine_click",
                    "routine": routine_key,
                    "step": step_index,
                    "step_label": step.get("label", ""),
                    "state": self.routine_state_snapshot(),
                }
            )
        finally:
            self.routine_click_pending = False

    def current_routine_step(self, routine_key: str, step_index: int) -> tuple[dict | None, list[dict], dict | None]:
        sequence = self.app_config.get("routine_sequences", {}).get(routine_key)
        if not sequence:
            return None, [], None
        steps = [step for step in sequence.get("steps", []) if isinstance(step, dict) and step.get("enabled", True)]
        if not steps or not (0 <= step_index < len(steps)):
            return sequence, steps, None
        return sequence, steps, steps[step_index]

    def advance_routine_after_click(self, routine_key: str, step_index: int, step: dict, total_steps: int) -> None:
        repeat_key = f"{routine_key}:{step_index}"
        repeat_total = max(1, int(step.get("repeat", 1) or 1))
        done = self.routine_step_repeat_count.get(repeat_key, 0) + 1
        if done < repeat_total:
            self.routine_step_repeat_count[repeat_key] = done
            self.routine_step_index[routine_key] = step_index
            return
        self.routine_step_repeat_count.pop(repeat_key, None)
        next_mode = str(step.get("next", "advance"))
        if next_mode == "stay":
            self.routine_step_index[routine_key] = step_index
        elif next_mode == "reset" or step_index + 1 >= total_steps:
            self.routine_step_index[routine_key] = 0
        else:
            self.routine_step_index[routine_key] = step_index + 1

    def routine_state_snapshot(self) -> dict:
        return {
            "steps": dict(self.routine_step_index),
            "repeats": dict(self.routine_step_repeat_count),
            "pending": self.routine_click_pending,
        }

    def finish_scheduled_mouse_move(self, hwnd: int, screen_x: int, screen_y: int, generation: int) -> None:
        if generation != self.auto_click_generation:
            return
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
        QTimer.singleShot(click_delay_ms, lambda: self.finish_scheduled_click(generation))

    def finish_scheduled_click(self, generation: int | None = None) -> None:
        if generation is not None and generation != self.auto_click_generation:
            return
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
            if context.get("kind") in {"discard", "riichi_discard"}:
                self.auto_last_discard_attempt = {
                    "tile_key": context.get("tile_key"),
                    "tile_label": context.get("tile_label"),
                    "at": self.auto_last_click_at,
                    "frames": 0,
                }
            if context.get("kind") == "riichi_discard":
                self.auto_riichi_discard_until = 0.0
            if context.get("kind") == "action" and context.get("action") in {"Chii", "Pon", "Kan"}:
                self.auto_call_settle_until = self.auto_last_click_at + self.auto_call_settle_seconds
            if context.get("kind") == "action" and context.get("action") == "Riichi":
                self.auto_riichi_discard_until = self.auto_last_click_at + max(6.0, self.auto_call_settle_seconds + 3.0)
            if context.get("kind") == "chii_option":
                self.auto_call_settle_until = self.auto_last_click_at + self.auto_call_settle_seconds
            self.write_auto_decision_log(
                {
                    "event": "auto_click",
                    "context": context,
                }
            )
            self.statusBar().showMessage("Auto: clique executado.")
            self.log_debug("Auto: clique executado apos atraso aleatorio.")
        finally:
            if not restarted and self.auto_click_pending:
                self.auto_click_pending = False
                self.auto_pending_context = None

    def pending_click_still_valid(self) -> bool:
        context = self.auto_pending_context or {}
        kind = context.get("kind")
        if kind in {"discard", "riichi_discard"}:
            state = self.latest_auto_state
            if state is None:
                return False
            if kind == "discard":
                current_tile = self.next_auto_discard_candidate(state)
            else:
                riichi_candidates = riichi_discard_candidates(state)
                current_tile = riichi_candidates[0] if riichi_candidates else None
            if current_tile is None:
                return False
            current_key = current_tile.base_key
            if current_key != context.get("tile_key"):
                return False
            player_region = self.configured_region_rect("player_hand")
            any_target_in_region = False
            for detection in self.latest_auto_detections:
                tile = tile_from_name(detection.name)
                if tile is None or tile.base_key != current_key:
                    continue
                if not player_region.contains(QPointF(detection.center_x, detection.center_y)):
                    continue
                any_target_in_region = True
                if self.detection_crosses_player_closed_line(detection.y1, detection.y2):
                    return True
            if kind == "riichi_discard":
                for detection in self.latest_auto_detections:
                    tile = tile_from_name(detection.name)
                    if tile is None or tile.base_key != current_key:
                        continue
                    if self.detection_in_riichi_choice_region(detection):
                        return True
            return any_target_in_region
        if kind == "action":
            action = context.get("action")
            state = self.latest_auto_state
            if not action:
                return False
            if action in {"Ron", "Tsumo"}:
                return self.active_button_probe(self.latest_auto_image or self.last_capture, str(action)) is not None
            if state is None:
                return False
            visible_actions = {
                "Chii": state.chii_button_visible,
                "Pon": state.pon_button_visible,
                "Kan": state.kan_button_visible,
                "Riichi": self.latest_auto_riichi_button_visible,
            }
            recommended_actions = {
                decision.action
                for decision in state.call_decisions
                if decision.recommended
            }
            return bool(
                visible_actions.get(action)
                and action in recommended_actions
                and self.active_button_probe(self.latest_auto_image or self.last_capture, str(action)) is not None
            )
        if kind == "skip":
            state = self.latest_auto_state
            if state is None:
                return False
            visible_actions = {
                "Chii": state.chii_button_visible,
                "Pon": state.pon_button_visible,
                "Kan": state.kan_button_visible,
            }
            skipped_actions = tuple(context.get("skipped_actions") or ())
            if not skipped_actions or not all(visible_actions.get(action) for action in skipped_actions):
                return False
            recommended_actions = {
                decision.action
                for decision in state.call_decisions
                if decision.recommended
            }
            return (
                not any(action in recommended_actions for action in ("Kan", "Pon", "Chii"))
                and self.skip_button_probe() is not None
            )
        if kind == "chii_option":
            if self.latest_auto_image is None or not self.detect_chii_choose_header(self.latest_auto_image):
                return False
            signature = tuple(context.get("signature") or ())
            if not signature:
                return False
            clusters = self.detect_chii_option_clusters(self.latest_auto_detections)
            return any(cluster.signature == signature for cluster in clusters)
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
        if old_context.get("kind") == "riichi_discard":
            if self.click_riichi_discard(self.latest_auto_detections, self.latest_auto_state):
                self.auto_last_click_at = time.perf_counter()
                self.auto_turn_frames = 0
                self.log_debug("Auto: novo descarte de Riichi agendado apos revalidacao.")

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
        status = self.probe_status(image, key)
        return bool(status and status["positive"])

    def probe_status(self, image: QImage | None, key: str) -> dict | None:
        probe = self.pixel_probe(key)
        if image is None or image.isNull() or probe is None:
            return None
        x = int(probe.get("x", 0))
        y = int(probe.get("y", 0))
        if not (0 <= x < image.width() and 0 <= y < image.height()):
            return None
        current = image.pixelColor(x, y)
        expected = QColor(str(probe.get("color", "#000000")))
        tolerance = int(probe.get("tolerance", 45))
        distance = self.color_distance(current, expected)
        mode = str(probe.get("mode", "match"))
        matched_color = distance <= tolerance
        positive = not matched_color if mode == "not_match" else matched_color
        return {
            "key": key,
            "label": probe.get("label", key),
            "mode": mode,
            "positive": positive,
            "distance": distance,
            "tolerance": tolerance,
            "current_color": current.name(),
            "expected_color": expected.name(),
            "x": x,
            "y": y,
        }

    def pixel_trigger_statuses(self, image: QImage | None) -> list[dict]:
        statuses = []
        for key, probe in self.app_config.get("pixel_probes", {}).items():
            if probe.get("mode") != "not_match":
                continue
            status = self.probe_status(image, key)
            if status is not None:
                statuses.append(status)
        return statuses

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
            "Ron": ("button_ron_1", "button_ron_2"),
            "Tsumo": ("button_tsumo_1", "button_tsumo_2"),
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
        defense_overlay = bool(state is not None and state.strategy_mode == "defense")
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
                if defense_overlay:
                    discard_color = QColor("#3B82F6")
                elif tile.base_key == primary_discard_key and not primary_discard_used:
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

    def draw_routine_debug_overlay(self, image: QImage) -> None:
        rules = self.app_config.get("routine_rules", {})
        if rules:
            painter = QPainter(image)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
            now = time.perf_counter()
            for rule_key, rule in rules.items():
                if not rule.get("enabled", True):
                    continue
                x = int(rule.get("x", 0))
                y = int(rule.get("y", 0))
                status = self.routine_rule_status(image, rule_key)
                positive = bool(status and status.get("positive"))
                color = QColor("#22C55E" if positive else "#EF4444")
                painter.setBrush(color)
                painter.setPen(QPen(QColor("#020617"), 2))
                painter.drawEllipse(QPointF(x, y), 9, 9)
                painter.setPen(QPen(color, 3))
                painter.drawLine(x - 18, y, x + 18, y)
                painter.drawLine(x, y - 18, x, y + 18)
                ready_since = self.routine_step_ready_since.get(f"rule:{rule_key}")
                elapsed = max(0.0, now - ready_since) if ready_since else 0.0
                delay_seconds = float(rule.get("delay_seconds", 1.0) or 0.0)
                label = (
                    f"Rot {rule.get('label', rule_key)} "
                    f"{'SIM' if positive else 'NAO'} {min(elapsed, delay_seconds):.1f}/{delay_seconds:.1f}s"
                )
                label_rect = QRectF(x + 12, max(0, y - 30), max(190, len(label) * 8 + 12), 24)
                background = QColor("#020617")
                background.setAlpha(210)
                painter.fillRect(label_rect, background)
                painter.setPen(color.lighter(140))
                painter.drawText(label_rect.adjusted(5, 0, -4, 0), Qt.AlignmentFlag.AlignVCenter, label)
            painter.end()
            return

        sequences = self.app_config.get("routine_sequences", {})
        if not sequences:
            return
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        drawn: set[tuple[str, str, str]] = set()
        for routine_key, sequence in sequences.items():
            if not sequence.get("enabled", True):
                continue
            steps = [step for step in sequence.get("steps", []) if isinstance(step, dict) and step.get("enabled", True)]
            if not steps:
                continue
            index = min(self.routine_step_index.get(routine_key, 0), len(steps) - 1)
            step = steps[index]
            probes = [("detecta", str(step.get("detect") or ""), str(step.get("condition", "match")))]
            if step.get("click_mode") == "probe" and step.get("click_probe"):
                probes.append(("clique", str(step.get("click_probe")), "match"))
            for role, probe_key, condition in probes:
                if not probe_key:
                    continue
                marker_id = (routine_key, role, probe_key)
                if marker_id in drawn:
                    continue
                drawn.add(marker_id)
                probe = self.routine_probe(probe_key)
                if probe is None:
                    continue
                x = int(probe.get("x", 0))
                y = int(probe.get("y", 0))
                status = self.routine_probe_status(image, probe_key, condition)
                positive = bool(status and status.get("positive")) if role == "detecta" else True
                color = QColor("#22C55E" if positive else "#EF4444")
                painter.setBrush(color)
                painter.setPen(QPen(QColor("#020617"), 2))
                painter.drawEllipse(QPointF(x, y), 9, 9)
                painter.setPen(QPen(color, 3))
                painter.drawLine(x - 18, y, x + 18, y)
                painter.drawLine(x, y - 18, x, y + 18)
                repeat_key = f"{routine_key}:{index}"
                repeat_done = self.routine_step_repeat_count.get(repeat_key, 0)
                repeat_total = int(step.get("repeat", 1) or 1)
                label = (
                    f"Rot {routine_key} {index + 1}/{len(steps)} "
                    f"{role}: {'SIM' if positive else 'NAO'} {repeat_done}/{repeat_total}"
                )
                label_rect = QRectF(x + 12, max(0, y - 30), max(170, len(label) * 8 + 12), 24)
                background = QColor("#020617")
                background.setAlpha(210)
                painter.fillRect(label_rect, background)
                painter.setPen(color.lighter(140))
                painter.drawText(label_rect.adjusted(5, 0, -4, 0), Qt.AlignmentFlag.AlignVCenter, label)
        painter.end()

    def draw_score_ocr_overlay(self, image: QImage) -> None:
        if not self.latest_score_ocr:
            return
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        for score in self.latest_score_ocr.values():
            region_key = str(score.get("region", ""))
            if not region_key:
                continue
            rect = self.configured_region_rect(region_key)
            value = score.get("value")
            text = str(value) if value is not None else str(score.get("text") or "OCR ?")
            if score.get("error"):
                error_text = str(score.get("error"))
                text = "OCR ?" if "sem digitos" in error_text else "OCR erro"
            label = f"{score.get('label', region_key)}: {text}"
            color = QColor(str(self.app_config["regions"].get(region_key, {}).get("color", "#38BDF8")))
            fill = QColor(color)
            fill.setAlpha(24)
            painter.setBrush(fill)
            painter.setPen(QPen(color, 3))
            painter.drawRect(rect)
            label_rect = QRectF(rect.left() + 4, max(0, rect.top() - 24), max(120, len(label) * 8 + 12), 22)
            background = QColor("#020617")
            background.setAlpha(210)
            painter.fillRect(label_rect, background)
            painter.setPen(color.lighter(145))
            painter.drawText(label_rect.adjusted(5, 0, -4, 0), Qt.AlignmentFlag.AlignVCenter, label)
        painter.end()

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

    def evaluate_capture_thresholds(self, image: QImage, detection_count: int) -> None:
        if not self.capture_mode_checkbox.isChecked():
            return
        first_threshold = int(self.app_config.get("capture_first_detection_threshold", 35))
        second_threshold = int(self.app_config.get("capture_second_detection_threshold", 75))
        if first_threshold > second_threshold:
            first_threshold, second_threshold = second_threshold, first_threshold

        if detection_count < first_threshold:
            if self.capture_threshold_stage != 1 or self.capture_waiting_for_reset:
                self.log_debug(
                    f"Modo captura: rearmou 1a foto; deteccoes cairam para {detection_count} (< {first_threshold})."
                )
            self.capture_threshold_stage = 1
            self.capture_waiting_for_reset = False
            return

        if self.capture_waiting_for_reset:
            return

        if self.capture_threshold_stage == 1 and detection_count >= first_threshold:
            if self.save_auto_capture_image(image, detection_count, first_threshold, "primeira"):
                self.capture_threshold_stage = 2
            return

        if self.capture_threshold_stage == 2 and detection_count >= second_threshold:
            if self.save_auto_capture_image(image, detection_count, second_threshold, "segunda"):
                self.capture_threshold_stage = 1
                self.capture_waiting_for_reset = True

    def save_auto_capture_image(self, image: QImage, detection_count: int, threshold: int, label: str) -> bool:
        if image.isNull():
            self.log_debug("Modo captura: frame vazio.")
            return
        RAW_DATASET_DIR.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        path = RAW_DATASET_DIR / f"mahjong_soul_auto_{label}_{detection_count}det_{timestamp}.png"
        if image.save(str(path), "PNG"):
            self.statusBar().showMessage(
                f"Modo captura: {label} foto salva com {detection_count} deteccoes."
            )
            self.log_debug(
                f"Modo captura salvou {path} | {label} foto | {detection_count} deteccoes >= {threshold}."
            )
            return True
        self.statusBar().showMessage("Modo captura: falha ao salvar screenshot.")
        return False

    def delete_all_logs(self) -> None:
        deleted = 0
        errors = 0
        if LOGS_DIR.exists():
            for path in LOGS_DIR.rglob("*"):
                if not path.is_file():
                    continue
                try:
                    if path == self.auto_decision_log_path:
                        self.auto_decision_log_path = None
                    path.unlink()
                    deleted += 1
                except OSError as error:
                    errors += 1
                    self.log_debug(f"Falha ao deletar log {path}: {type(error).__name__}: {error}")
        self.auto_decision_log_events = 0
        self.auto_decision_log_last_signature = ""
        self.auto_decision_log_last_at = 0.0
        if self.auto_play_checkbox.isChecked():
            self.start_auto_decision_log()
        message = f"Logs deletados: {deleted}" + (f" | falhas: {errors}" if errors else "")
        self.statusBar().showMessage(message)
        self.log_debug(message)

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

    def open_terminal(self) -> None:
        self.terminal_window.show()
        self.terminal_window.raise_()
        self.terminal_window.activateWindow()

    def open_region_config(self) -> None:
        if self.region_config_dialog is None:
            self.region_config_dialog = RegionConfigDialog(self.app_config, self)

        self.region_config_dialog.set_preview_image(self.last_capture)
        self.region_config_dialog.show()
        self.region_config_dialog.raise_()
        self.region_config_dialog.activateWindow()

    def open_routine_config(self) -> None:
        if self.routine_config_dialog is None:
            self.routine_config_dialog = RoutineConfigDialog(self.app_config, self)

        self.routine_config_dialog.set_preview_image(self.last_capture)
        self.routine_config_dialog.show()
        self.routine_config_dialog.raise_()
        self.routine_config_dialog.activateWindow()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self.capture_once()

    def closeEvent(self, event) -> None:  # noqa: N802
        self.finalize_auto_decision_log("app_close")
        self.prediction_thread.quit()
        self.prediction_thread.wait(3000)
        self.terminal_window.hide()
        self.screen_capture.close()
        super().closeEvent(event)


def main() -> int:
    _enable_dpi_awareness()
    app = QApplication(sys.argv)
    apply_dark_theme(app)
    window = MainWindow()
    def handled(callback) -> object:
        def run() -> bool:
            callback()
            return True

        return run

    hotkey_specs = [
        (HOTKEY_ID_SAVE_SCREENSHOT, VK_F3, handled(window.save_screenshot), "F3", True),
        (HOTKEY_ID_AUTO_PLAY, VK_TAB, handled(window.toggle_auto_play), "Tab", True),
        (HOTKEY_ID_PREVIEW, VK_V, handled(lambda: MainWindow.toggle_checkbox_shortcut(window.preview_checkbox)), "V", True),
        (HOTKEY_ID_CAPTURE, VK_C, handled(lambda: MainWindow.toggle_checkbox_shortcut(window.capture_mode_checkbox)), "C", True),
        (HOTKEY_ID_ROUTINES, VK_R, handled(lambda: MainWindow.toggle_checkbox_shortcut(window.routines_checkbox)), "R", True),
        (HOTKEY_ID_AREAS_OVERLAY, VK_A, handled(lambda: MainWindow.toggle_checkbox_shortcut(window.show_regions_checkbox)), "A", True),
        (HOTKEY_ID_OPTIONS, VK_O, handled(window.open_options_menu), "O", True),
        (HOTKEY_ID_ANNOTATOR, VK_N, handled(window.open_annotator), "N", True),
        (HOTKEY_ID_TRAINING, VK_T, handled(window.open_training), "T", True),
        (HOTKEY_ID_REGION_CONFIG, VK_E, handled(window.open_region_config), "E", True),
        (HOTKEY_ID_TERMINAL, VK_M, handled(window.open_terminal), "M", True),
    ]
    hotkey_filters = [
        GlobalHotkeyFilter(hotkey_id, virtual_key, callback, auto_register=not predict_gated)
        for hotkey_id, virtual_key, callback, _label, predict_gated in hotkey_specs
    ]
    for hotkey_filter in hotkey_filters:
        app.installNativeEventFilter(hotkey_filter)
        app.aboutToQuit.connect(hotkey_filter.unregister)

    def update_predict_gated_hotkeys(*_args) -> None:
        enabled = window.predict_checkbox.isChecked() and not window.annotation_shortcuts_active()
        for hotkey_filter, spec in zip(hotkey_filters, hotkey_specs):
            predict_gated = spec[4]
            if predict_gated:
                hotkey_filter.set_registered(enabled)

    window.predict_checkbox.stateChanged.connect(update_predict_gated_hotkeys)
    app.focusChanged.connect(update_predict_gated_hotkeys)
    update_predict_gated_hotkeys()

    registered = [
        spec[3]
        for hotkey_filter, spec in zip(hotkey_filters, hotkey_specs)
        if hotkey_filter.registered
    ]
    failed = [
        spec[3]
        for hotkey_filter, spec in zip(hotkey_filters, hotkey_specs)
        if not hotkey_filter.registered and not spec[4]
    ]
    if failed:
        window.statusBar().showMessage(
            f"Atalhos globais ativos: {', '.join(registered) or '-'} | indisponiveis: {', '.join(failed)}"
        )
    else:
        window.statusBar().showMessage("Atalhos globais: modos so com Predict ativo e fora do Anotador.")

    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
