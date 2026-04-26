from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import sys
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import mss
from PyQt6.QtCore import QAbstractNativeEventFilter, QRectF, Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QImage, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from mahjong_master.annotator import AnnotatorWindow
from mahjong_master.game_analyzer import GameAnalyzer, detections_from_yolo_result
from mahjong_master.mahjong_logic import HandState, tile_from_name
from mahjong_master.theme import apply_dark_theme


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RAW_DATASET_DIR = PROJECT_ROOT / "dataset" / "raw"
RUNS_DIR = PROJECT_ROOT / "runs" / "detect"
DEFAULT_WINDOW_TITLE = "MahjongSoul-Steam"
HOTKEY_ID_SAVE_SCREENSHOT = 1
VK_F3 = 0x72
WM_HOTKEY = 0x0312
CAPTURE_WIDTH = 1592
CAPTURE_HEIGHT = 933
PREDICT_IMAGE_SIZE = 1592

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
        self.capture_count = 0
        self.predict_count = 0
        self.last_game_summary = "Hand: - | Yaku provavel: -"
        self.annotator_window: AnnotatorWindow | None = None
        self.training_window = None

        self.window_combo = QComboBox()
        self.window_combo.setMinimumWidth(160)
        self.window_combo.currentIndexChanged.connect(self.capture_once)

        self.refresh_button = QPushButton("Atualizar janelas")
        self.refresh_button.clicked.connect(self.refresh_windows)

        self.save_button = QPushButton("Salvar screenshot (F3)")
        self.save_button.clicked.connect(self.save_screenshot)

        self.annotator_button = QPushButton("Abrir anotador")
        self.annotator_button.clicked.connect(self.open_annotator)

        self.training_button = QPushButton("Abrir treino")
        self.training_button.clicked.connect(self.open_training)

        self.model_combo = QComboBox()
        self.model_combo.setMinimumWidth(180)
        self.model_combo.currentIndexChanged.connect(self.unload_prediction_model)

        self.predict_checkbox = QCheckBox("Predict")
        self.predict_checkbox.stateChanged.connect(self.predict_setting_changed)

        self.predict_once_button = QPushButton("Predict 1x")
        self.predict_once_button.clicked.connect(self.predict_once_clicked)

        self.predict_fps_input = QDoubleSpinBox()
        self.predict_fps_input.setRange(0.2, 60.0)
        self.predict_fps_input.setSingleStep(0.5)
        self.predict_fps_input.setValue(30.0)
        self.predict_fps_input.setSuffix(" FPS")
        self.predict_fps_input.valueChanged.connect(self.predict_setting_changed)

        self.predict_conf_input = QDoubleSpinBox()
        self.predict_conf_input.setRange(0.01, 0.99)
        self.predict_conf_input.setSingleStep(0.05)
        self.predict_conf_input.setValue(0.50)
        self.predict_conf_input.valueChanged.connect(self.predict_parameter_changed)

        self.pause_after_predict_checkbox = QCheckBox("Pausar apos proximo predict")

        self.preview_checkbox = QCheckBox("Preview")
        self.preview_checkbox.setChecked(True)
        self.preview_checkbox.stateChanged.connect(self.preview_setting_changed)

        self.debug_checkbox = QCheckBox("DEBUG")
        self.debug_checkbox.stateChanged.connect(self.debug_setting_changed)

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
        self.game_summary_log.setMinimumHeight(210)
        self.game_summary_log.document().setMaximumBlockCount(250)
        self.game_summary_log.setPlaceholderText("Resumo da mao aparece aqui apos o predict.")

        self.debug_log = QPlainTextEdit()
        self.debug_log.setReadOnly(True)
        self.debug_log.setMaximumHeight(95)
        self.debug_log.document().setMaximumBlockCount(250)
        self.debug_log.setPlaceholderText("Logs tecnicos aparecem aqui com DEBUG ligado.")
        self.debug_log.setVisible(False)

        window_controls = QHBoxLayout()
        window_controls.setContentsMargins(0, 0, 0, 0)
        window_controls.addWidget(QLabel("Janela:"))
        window_controls.addWidget(self.window_combo, stretch=1)
        self.refresh_button.setText("Atualizar")
        window_controls.addWidget(self.refresh_button)

        action_controls = QHBoxLayout()
        action_controls.setContentsMargins(0, 0, 0, 0)
        self.save_button.setText("Screenshot F3")
        self.annotator_button.setText("Anotar")
        self.training_button.setText("Treino")
        action_controls.addWidget(self.save_button)
        action_controls.addWidget(self.annotator_button)
        action_controls.addWidget(self.training_button)
        action_controls.addStretch(1)

        model_controls = QHBoxLayout()
        model_controls.setContentsMargins(0, 0, 0, 0)
        model_controls.addWidget(QLabel("Modelo:"))
        model_controls.addWidget(self.model_combo, stretch=1)

        predict_controls = QHBoxLayout()
        predict_controls.setContentsMargins(0, 0, 0, 0)
        predict_controls.addWidget(self.predict_checkbox)
        predict_controls.addWidget(self.predict_once_button)
        predict_controls.addWidget(self.preview_checkbox)
        predict_controls.addWidget(self.debug_checkbox)
        predict_controls.addWidget(QLabel("Taxa:"))
        predict_controls.addWidget(self.predict_fps_input)
        predict_controls.addStretch(1)

        predict_options = QHBoxLayout()
        predict_options.setContentsMargins(0, 0, 0, 0)
        predict_options.addWidget(QLabel("Conf:"))
        predict_options.addWidget(self.predict_conf_input)
        predict_options.addWidget(self.pause_after_predict_checkbox)
        predict_options.addStretch(1)

        root_layout = QVBoxLayout()
        root_layout.setContentsMargins(8, 8, 8, 8)
        root_layout.setSpacing(6)
        root_layout.addLayout(window_controls)
        root_layout.addLayout(action_controls)
        root_layout.addLayout(model_controls)
        root_layout.addLayout(predict_controls)
        root_layout.addLayout(predict_options)
        root_layout.addWidget(self.preview_label, stretch=1)
        root_layout.addWidget(self.debug_state_label)
        root_layout.addWidget(self.game_summary_log, stretch=1)
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
        self.log_debug("App iniciado.")

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
        self.debug_state_label.setVisible(enabled)
        self.debug_log.setVisible(enabled)
        if enabled:
            self.debug_log.appendPlainText("[DEBUG ligado]")
        else:
            self.debug_log.clear()

    def refresh_debug_state(self) -> None:
        state = "ON" if self.predict_checkbox.isChecked() else "OFF"
        self.debug_state_label.setText(
            f"Capturas: {self.capture_count} | Predicts: {self.predict_count} | Predict: {state} | "
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
        self.refresh_debug_state()

    def predict_setting_changed(self, *_args) -> None:
        if self.predict_checkbox.isChecked() and self.selected_model_path() is None:
            self.predict_checkbox.setChecked(False)
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

    def predict_parameter_changed(self, *_args) -> None:
        self.predict_runtime_error = None
        self.log_debug(f"Parametros alterados | conf={self.predict_conf_input.value():.2f}")
        if self.predict_checkbox.isChecked():
            self.capture_once()

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
            from ultralytics import YOLO

            started_at = time.perf_counter()
            self.log_debug(f"Carregando modelo: {model_path}")
            self.prediction_model = YOLO(str(model_path))
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
        state = GameAnalyzer(CAPTURE_WIDTH, CAPTURE_HEIGHT).analyze(detections)
        self.update_game_state(state)
        output = image.copy()
        self.draw_predictions(output, result, state)
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
        for x1, y1, x2, y2, label, name in detections:
            tile = tile_from_name(name)
            is_player_tile = ((y1 + y2) / 2) >= CAPTURE_HEIGHT * 0.64
            is_discard_candidate = bool(tile and is_player_tile and discard_counts[tile.base_key] > 0)
            if is_discard_candidate:
                discard_counts[tile.base_key] -= 1
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(239, 68, 68, 95))
                painter.drawRect(int(x1), int(y1), int(x2 - x1), int(y2 - y1))

            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor("#EF4444" if is_discard_candidate else "#22C55E"), 2))
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
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QColor("#FFFFFF"))
            painter.drawText(
                label_rect.adjusted(6, 0, -6, 0),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                label,
            )

        painter.end()

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
            candidates.extend(
                [
                    QRectF(box_rect.left(), box_rect.top() - gap - label_height - shift, label_width, label_height),
                    QRectF(box_rect.left(), box_rect.bottom() + gap + shift, label_width, label_height),
                    QRectF(box_rect.right() + gap + shift, box_rect.top(), label_width, label_height),
                    QRectF(box_rect.left() - gap - label_width - shift, box_rect.top(), label_width, label_height),
                ]
            )

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
    app.installNativeEventFilter(hotkey_filter)
    app.aboutToQuit.connect(hotkey_filter.unregister)

    if hotkey_filter.registered:
        window.statusBar().showMessage("F3 salva screenshot mesmo com foco no jogo.")
    else:
        window.statusBar().showMessage("Nao foi possivel registrar F3 como atalho global.")

    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
