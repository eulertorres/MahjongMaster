from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path

from PyQt6.QtCore import QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QBrush, QColor, QKeySequence, QPen, QPixmap, QShortcut
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGraphicsPixmapItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsTextItem,
    QGraphicsView,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QKeySequenceEdit,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from mahjong_master.tile_classes import TILE_CLASSES


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RAW_DATASET_DIR = PROJECT_ROOT / "dataset" / "raw"
DATASET_DIR = PROJECT_ROOT / "dataset"
RUNS_DIR = PROJECT_ROOT / "runs" / "detect"
CONFIG_DIR = PROJECT_ROOT / "config"
SHORTCUTS_CONFIG_PATH = CONFIG_DIR / "shortcuts.json"
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg"}
DATASET_SPLITS = ("train", "val", "test")
RECOMMENDED_SPLIT_PERCENTAGES = {
    "train": 80.0,
    "val": 15.0,
    "test": 5.0,
}


@dataclass
class Annotation:
    class_id: int | None
    rect: QRectF


@dataclass
class HelperBox:
    rect: QRectF
    class_id: int | None = None
    confidence: float = 0.0


class AnnotationRectItem(QGraphicsRectItem):
    resize_handle_size = 12

    def __init__(
        self,
        index: int,
        annotation: Annotation,
        selected: bool,
        editable: bool,
        changed_callback,
    ) -> None:
        rect = annotation.rect.normalized()
        super().__init__(0, 0, rect.width(), rect.height())
        self.index = index
        self.editable = editable
        self.changed_callback = changed_callback
        self._resizing = False
        self._resize_start_pos = QPointF()
        self._resize_start_rect = QRectF()
        self.setPos(rect.left(), rect.top())
        self.setFlag(QGraphicsRectItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setFlag(QGraphicsRectItem.GraphicsItemFlag.ItemSendsGeometryChanges, True)
        if editable:
            self.setFlag(QGraphicsRectItem.GraphicsItemFlag.ItemIsMovable, True)
            self.setAcceptHoverEvents(True)
        self.setData(0, index)
        self.setSelected(selected)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if self.editable and event.button() == Qt.MouseButton.LeftButton and self.is_resize_hit(event.pos()):
            self._resizing = True
            self._resize_start_pos = event.scenePos()
            self._resize_start_rect = QRectF(self.rect())
            self.setSelected(True)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._resizing:
            delta = event.scenePos() - self._resize_start_pos
            width = max(6.0, self._resize_start_rect.width() + delta.x())
            height = max(6.0, self._resize_start_rect.height() + delta.y())
            self.setRect(0, 0, width, height)
            self.notify_changed()
            event.accept()
            return
        super().mouseMoveEvent(event)
        if self.editable:
            self.notify_changed()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        super().mouseReleaseEvent(event)
        if self._resizing:
            self._resizing = False
        if self.editable:
            self.notify_changed()

    def hoverMoveEvent(self, event) -> None:  # noqa: N802
        if self.editable and self.is_resize_hit(event.pos()):
            self.setCursor(Qt.CursorShape.SizeFDiagCursor)
        elif self.editable:
            self.setCursor(Qt.CursorShape.SizeAllCursor)
        super().hoverMoveEvent(event)

    def is_resize_hit(self, pos: QPointF) -> bool:
        rect = self.rect()
        return (
            rect.width() - self.resize_handle_size <= pos.x() <= rect.width() + 4
            and rect.height() - self.resize_handle_size <= pos.y() <= rect.height() + 4
        )

    def notify_changed(self) -> None:
        rect = self.rect().normalized()
        pos = self.pos()
        self.changed_callback(self.index, QRectF(pos.x(), pos.y(), rect.width(), rect.height()))


class AnnotationView(QGraphicsView):
    box_created = pyqtSignal(QRectF)

    def __init__(self) -> None:
        super().__init__()
        self.setRenderHints(self.renderHints())
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setMouseTracking(True)
        self.setScene(QGraphicsScene(self))
        self.image_item: QGraphicsPixmapItem | None = None
        self.image_rect = QRectF()
        self.start_pos: QPointF | None = None
        self.draft_item: QGraphicsRectItem | None = None
        self.click_handler = None
        self.double_click_handler = None

    def load_pixmap(self, pixmap: QPixmap) -> None:
        self.scene().clear()
        self.image_item = self.scene().addPixmap(pixmap)
        self.image_rect = QRectF(pixmap.rect())
        self.scene().setSceneRect(self.image_rect)
        self.fitInView(self.image_rect, Qt.AspectRatioMode.KeepAspectRatio)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if not self.image_rect.isNull():
            self.fitInView(self.image_rect, Qt.AspectRatioMode.KeepAspectRatio)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton or self.image_rect.isNull():
            super().mousePressEvent(event)
            return

        pos = self._clamped_scene_pos(event.position().toPoint())
        if pos is None:
            super().mousePressEvent(event)
            return

        scene_item = self.itemAt(event.position().toPoint())
        if isinstance(scene_item, AnnotationRectItem) and scene_item.editable:
            super().mousePressEvent(event)
            return

        if self.click_handler is not None and self.click_handler(pos):
            return

        self.start_pos = pos
        self.draft_item = self.scene().addRect(
            QRectF(pos, pos),
            QPen(QColor("#38BDF8"), 2, Qt.PenStyle.DashLine),
        )

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self.start_pos is None or self.draft_item is None:
            super().mouseMoveEvent(event)
            return

        pos = self._clamped_scene_pos(event.position().toPoint())
        if pos is None:
            return

        rect = QRectF(self.start_pos, pos).normalized()
        self.draft_item.setRect(rect)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton or self.start_pos is None:
            super().mouseReleaseEvent(event)
            return

        pos = self._clamped_scene_pos(event.position().toPoint())
        rect = QRectF(self.start_pos, pos or self.start_pos).normalized()

        if self.draft_item is not None:
            self.scene().removeItem(self.draft_item)

        self.start_pos = None
        self.draft_item = None

        if rect.width() >= 6 and rect.height() >= 6:
            self.box_created.emit(rect)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton or self.image_rect.isNull():
            super().mouseDoubleClickEvent(event)
            return

        pos = self._clamped_scene_pos(event.position().toPoint())
        if pos is None:
            super().mouseDoubleClickEvent(event)
            return

        if self.double_click_handler is not None and self.double_click_handler(pos):
            return

        super().mouseDoubleClickEvent(event)

    def _clamped_scene_pos(self, view_pos) -> QPointF | None:
        pos = self.mapToScene(view_pos)
        if not self.image_rect.contains(pos):
            pos.setX(min(max(pos.x(), self.image_rect.left()), self.image_rect.right()))
            pos.setY(min(max(pos.y(), self.image_rect.top()), self.image_rect.bottom()))

        return pos if self.image_rect.contains(pos) else None


class AnnotatorWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("MahjongMaster - Anotador YOLO")
        self.resize(1320, 820)

        self.images = self._raw_images()
        self.current_index = 0
        self.current_image_path: Path | None = None
        self.current_pixmap = QPixmap()
        self.annotations: list[Annotation] = []
        self.helper_boxes: list[HelperBox] = []
        self.annotation_items: list[QGraphicsRectItem] = []
        self.annotation_labels: list[QGraphicsTextItem] = []
        self.helper_items: list[QGraphicsRectItem] = []
        self.helper_labels: list[QGraphicsTextItem] = []
        self.selected_annotation_index: int | None = None
        self.annotation_model = None
        self.annotation_model_path: Path | None = None
        self.shortcuts: list[QShortcut] = []
        self.shortcut_edits: dict[str, QKeySequenceEdit] = {}
        self.shortcut_config = self.load_shortcut_config()
        self.pending_selected_suit: str | None = None

        self.image_list = QListWidget()
        self.image_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.image_list.currentRowChanged.connect(self.load_image_by_index)
        self.image_list.setMinimumWidth(280)
        self.image_list.setSpacing(1)
        self.populate_image_list()

        self.train_summary_list = QListWidget()
        self.train_summary_list.setMinimumHeight(180)
        self.refresh_train_summary()

        self.view = AnnotationView()
        self.view.click_handler = self.handle_image_click
        self.view.double_click_handler = self.handle_image_double_click
        self.view.box_created.connect(self.add_annotation)
        self.view.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        self.helper_mode_combo = QComboBox()
        self.helper_mode_combo.addItem("Manual", "manual")
        self.helper_mode_combo.addItem("Box-helper", "box_helper")
        self.helper_mode_combo.addItem("Predizer e revisar", "predict_review")
        self.helper_mode_combo.currentIndexChanged.connect(self.annotation_mode_changed)

        self.model_combo = QComboBox()
        self.model_combo.setMinimumWidth(220)
        self.model_combo.currentIndexChanged.connect(self.unload_annotation_model)

        self.conf_input = QDoubleSpinBox()
        self.conf_input.setRange(0.01, 0.99)
        self.conf_input.setSingleStep(0.05)
        self.conf_input.setDecimals(2)
        self.conf_input.setValue(0.50)
        self.conf_input.setMaximumWidth(84)

        self.detect_button = QPushButton("Detectar boxes")
        self.detect_button.clicked.connect(self.detect_boxes_for_current_mode)

        self.class_combo = QComboBox()
        self.class_combo.addItems(TILE_CLASSES)
        self.class_combo.currentIndexChanged.connect(self.class_changed)

        self.class_buttons = QButtonGroup(self)
        self.class_buttons.idClicked.connect(self.select_class)
        self.class_tabs = self._build_class_tabs()

        self.train_radio = QRadioButton("train")
        self.val_radio = QRadioButton("val")
        self.test_radio = QRadioButton("test")
        self.train_radio.setChecked(True)

        self.save_button = QPushButton("Salvar")
        self.save_button.clicked.connect(self.save_current)
        self.next_button = QPushButton("Proxima")
        self.next_button.clicked.connect(self.next_image)
        self.delete_button = QPushButton("Remover box")
        self.delete_button.clicked.connect(self.delete_selected)
        self.undo_button = QPushButton("Desfazer")
        self.undo_button.clicked.connect(self.undo_last)
        self.shortcuts_panel = self._build_shortcuts_panel()

        right = QVBoxLayout()
        right.setContentsMargins(6, 0, 0, 0)
        right.setSpacing(4)
        helper_group = QGroupBox("Modelo auxiliar")
        helper_layout = QFormLayout()
        helper_layout.setContentsMargins(6, 8, 6, 6)
        helper_layout.setHorizontalSpacing(6)
        helper_layout.setVerticalSpacing(4)
        mode_row = QHBoxLayout()
        mode_row.setSpacing(4)
        mode_row.addWidget(QLabel("Modo"))
        mode_row.addWidget(self.helper_mode_combo, stretch=1)
        mode_row.addWidget(QLabel("Conf"))
        mode_row.addWidget(self.conf_input)
        helper_layout.addRow(mode_row)
        helper_layout.addRow(self.model_combo)
        helper_actions = QHBoxLayout()
        helper_actions.setSpacing(4)
        helper_actions.addWidget(self.detect_button)
        helper_actions.addStretch(1)
        helper_layout.addRow(helper_actions)
        helper_group.setLayout(helper_layout)
        right.addWidget(helper_group)
        class_header = QHBoxLayout()
        class_header.setSpacing(4)
        class_header.addWidget(QLabel("Classe"))
        class_header.addWidget(self.class_combo, stretch=1)
        right.addLayout(class_header)
        right.addWidget(self.class_tabs, stretch=1)
        right.addWidget(self.shortcuts_panel)
        destination_row = QHBoxLayout()
        destination_row.setSpacing(8)
        destination_row.addWidget(QLabel("Destino"))
        destination_row.addWidget(self.train_radio)
        destination_row.addWidget(self.val_radio)
        destination_row.addWidget(self.test_radio)
        destination_row.addStretch(1)
        right.addLayout(destination_row)
        action_row = QHBoxLayout()
        action_row.setSpacing(4)
        action_row.addWidget(self.save_button)
        action_row.addWidget(self.next_button)
        action_row.addWidget(self.delete_button)
        action_row.addWidget(self.undo_button)
        right.addLayout(action_row)

        left = QVBoxLayout()
        left.addWidget(QLabel("Screenshots"))
        left.addWidget(self.image_list, stretch=3)
        left.addWidget(QLabel("Pecas catalogadas"))
        left.addWidget(self.train_summary_list, stretch=2)

        left_panel = QWidget()
        left_panel.setMaximumWidth(340)
        left_panel.setLayout(left)

        content = QHBoxLayout()
        content.addWidget(left_panel)
        content.addWidget(self.view, stretch=1)
        content.addLayout(right)

        root = QWidget()
        root.setLayout(content)
        self.setCentralWidget(root)

        self.statusBar().showMessage("Arraste na imagem para marcar uma peca.")
        self.refresh_model_list()
        self._install_shortcuts()

        if self.images:
            self.image_list.setCurrentRow(0)
        else:
            self.statusBar().showMessage(f"Nenhuma imagem encontrada em {RAW_DATASET_DIR}")

    def shortcut_actions(self) -> dict[str, tuple[str, str, object]]:
        actions = {
            "select_man": ("Man", "A", lambda: self.select_suit("man")),
            "select_pin": ("Pin", "S", lambda: self.select_suit("pin")),
            "select_sou": ("Sou", "D", lambda: self.select_suit("sou")),
            "select_honors": ("Honras", "F", self.select_honors),
            "select_wind_east": ("East", "Q", lambda: self.select_honor("wind_east")),
            "select_wind_south": ("South", "W", lambda: self.select_honor("wind_south")),
            "select_wind_west": ("West", "E", lambda: self.select_honor("wind_west")),
            "select_wind_north": ("North", "T", lambda: self.select_honor("wind_north")),
            "select_dragon_white": (
                "White",
                "Y",
                lambda: self.select_honor("dragon_white"),
            ),
            "select_dragon_green": (
                "Green",
                "U",
                lambda: self.select_honor("dragon_green"),
            ),
            "select_dragon_red": ("Red", "I", lambda: self.select_honor("dragon_red")),
            "select_tile_back": ("Virada", "V", lambda: self.select_honor("tile_back")),
            "select_red_five": ("5 vermelho", "R", self.select_red_five),
            "save": ("Salvar", "Return", self.save_current),
            "delete": ("Remover box", "Delete", self.delete_selected),
            "undo": ("Desfazer", "Ctrl+Z", self.undo_last),
            "previous_image": ("Imagem anterior", "Z", self.previous_image),
            "next_image": ("Proxima imagem", "X", self.next_image),
        }
        for number in range(1, 10):
            actions[f"number_{number}"] = (
                f"Numero {number}",
                str(number),
                lambda n=number: self.select_number(n),
            )

        return actions

    def _build_shortcuts_panel(self) -> QWidget:
        group = QGroupBox("Atalhos")
        form = QFormLayout()
        form.setContentsMargins(6, 8, 6, 6)
        form.setHorizontalSpacing(6)
        form.setVerticalSpacing(3)

        for action_id, (label, default, _callback) in self.shortcut_actions().items():
            edit = QKeySequenceEdit(QKeySequence(self.shortcut_config.get(action_id, default)))
            edit.editingFinished.connect(self.rebuild_shortcuts)
            self.shortcut_edits[action_id] = edit
            form.addRow(label, edit)

        group.setLayout(form)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMaximumHeight(220)
        scroll.setWidget(group)
        return scroll

    def _raw_images(self) -> list[Path]:
        RAW_DATASET_DIR.mkdir(parents=True, exist_ok=True)
        return sorted(
            path
            for path in RAW_DATASET_DIR.iterdir()
            if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
        )

    def populate_image_list(self, selected_index: int | None = None) -> None:
        self.image_list.blockSignals(True)
        self.image_list.clear()
        for path in self.images:
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, str(path))
            self.image_list.addItem(item)
            self.update_image_list_item(self.image_list.count() - 1)
        self.image_list.blockSignals(False)

        if self.images and selected_index is not None:
            index = min(max(selected_index, 0), len(self.images) - 1)
            self.image_list.setCurrentRow(index)

    def update_image_list_item(self, index: int) -> None:
        if index < 0 or index >= len(self.images):
            return

        image_path = self.images[index]
        item = self.image_list.item(index)
        split = self.split_for_image(image_path)
        color = {
            "train": "#22C55E",
            "val": "#60A5FA",
            "test": "#C084FC",
            None: "#E5E7EB",
        }[split]
        suffix = f" [{split}]" if split else ""

        row = QWidget()
        row_layout = QHBoxLayout()
        row_layout.setContentsMargins(2, 1, 2, 1)
        row_layout.setSpacing(4)

        delete_button = QPushButton("X")
        delete_button.setFixedSize(20, 20)
        delete_button.setToolTip("Deletar imagem e labels salvos")
        delete_button.setStyleSheet(
            """
            QPushButton {
                background: transparent;
                border: none;
                color: #EF4444;
                font-weight: 800;
                padding: 0;
            }
            QPushButton:hover {
                background: #3F1117;
                border: 1px solid #7F1D1D;
                border-radius: 3px;
            }
            """
        )
        delete_button.clicked.connect(lambda _checked=False, path=image_path: self.delete_image(path))

        label = QLabel(f"{image_path.name}{suffix}")
        label.setStyleSheet(f"color: {color}; background: transparent;")
        label.setToolTip("Ainda nao salva" if split is None else f"Salva para {split}")
        label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        row_layout.addWidget(delete_button)
        row_layout.addWidget(label, stretch=1)
        row.setLayout(row_layout)

        item.setSizeHint(row.sizeHint())
        item.setToolTip(label.toolTip())
        self.image_list.setItemWidget(item, row)

    def split_for_image(self, image_path: Path) -> str | None:
        for split in DATASET_SPLITS:
            label_path = DATASET_DIR / "labels" / split / f"{image_path.stem}.txt"
            image_copy = DATASET_DIR / "images" / split / image_path.name
            if label_path.exists() or image_copy.exists():
                return split

        return None

    def refresh_image_statuses(self) -> None:
        for index, image_path in enumerate(self.images):
            item = self.image_list.item(index)
            if item is not None:
                self.update_image_list_item(index)

    def refresh_train_summary(self) -> None:
        if not hasattr(self, "train_summary_list"):
            return

        counts_by_split = {
            split: [0 for _ in TILE_CLASSES]
            for split in DATASET_SPLITS
        }
        image_counts_by_split = {
            split: self.count_split_images(split)
            for split in DATASET_SPLITS
        }
        for split, counts in counts_by_split.items():
            labels_dir = DATASET_DIR / "labels" / split
            if not labels_dir.exists():
                continue
            for label_path in sorted(labels_dir.glob("*.txt")):
                for line in label_path.read_text(encoding="utf-8").splitlines():
                    parts = line.split()
                    if not parts:
                        continue
                    try:
                        class_id = int(parts[0])
                    except ValueError:
                        continue
                    if 0 <= class_id < len(counts):
                        counts[class_id] += 1

        self.train_summary_list.clear()
        train_total = sum(counts_by_split["train"])
        val_total = sum(counts_by_split["val"])
        test_total = sum(counts_by_split["test"])
        grand_total = train_total + val_total + test_total
        image_total = sum(image_counts_by_split.values())
        self.train_summary_list.addItem(
            "Recomendado: "
            + " | ".join(
                f"{split} {RECOMMENDED_SPLIT_PERCENTAGES[split]:.0f}%"
                for split in DATASET_SPLITS
            )
        )
        self.train_summary_list.addItem(
            "Imagens: "
            + " | ".join(
                self.split_count_summary(
                    split,
                    image_counts_by_split[split],
                    image_total,
                )
                for split in DATASET_SPLITS
            )
        )
        self.train_summary_list.addItem(
            "Anotacoes: "
            + " | ".join(
                self.split_count_summary(
                    split,
                    sum(counts_by_split[split]),
                    grand_total,
                )
                for split in DATASET_SPLITS
            )
            + f" | geral {grand_total}"
        )

        for class_id, class_name in enumerate(TILE_CLASSES):
            train_count = counts_by_split["train"][class_id]
            val_count = counts_by_split["val"][class_id]
            test_count = counts_by_split["test"][class_id]
            total = train_count + val_count + test_count
            item = QListWidgetItem(
                f"{class_name}: train {train_count} | val {val_count} | test {test_count} | total {total}"
            )
            if total == 0:
                item.setForeground(QBrush(QColor("#EF4444")))
                item.setToolTip("Ainda sem exemplos catalogados")
            elif train_count == 0 or val_count == 0 or test_count == 0:
                item.setForeground(QBrush(QColor("#F59E0B")))
                item.setToolTip("Falta exemplo em train, val ou test")
            else:
                item.setForeground(QBrush(QColor("#E5E7EB")))
                item.setToolTip("Classe presente em train, val e test")
            self.train_summary_list.addItem(item)

    @staticmethod
    def percentage(value: int, total: int) -> str:
        if total <= 0:
            return "0.0%"
        return f"{value / total * 100:.1f}%"

    def split_count_summary(self, split: str, value: int, total: int) -> str:
        current_percentage = self.percentage(value, total)
        recommended_percentage = RECOMMENDED_SPLIT_PERCENTAGES[split]
        return f"{split} {value} ({current_percentage}, rec {recommended_percentage:.0f}%)"

    def count_split_images(self, split: str) -> int:
        image_dir = DATASET_DIR / "images" / split
        if not image_dir.exists():
            return 0
        return sum(
            1
            for path in image_dir.iterdir()
            if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
        )

    def _build_class_tabs(self) -> QTabWidget:
        tabs = QTabWidget()
        groups = {
            "Man": self.numbered_tile_entries("man"),
            "Pin": self.numbered_tile_entries("pin"),
            "Sou": self.numbered_tile_entries("sou"),
            "Honras": [
                ("wind_east", "East"),
                ("wind_south", "South"),
                ("wind_west", "West"),
                ("wind_north", "North"),
                ("dragon_white", "White"),
                ("dragon_green", "Green"),
                ("dragon_red", "Red"),
                ("tile_back", "Virada"),
            ],
        }

        for title, entries in groups.items():
            panel = QWidget()
            grid = QGridLayout()
            grid.setContentsMargins(4, 4, 4, 4)
            grid.setHorizontalSpacing(4)
            grid.setVerticalSpacing(4)
            for index, (class_name, label) in enumerate(entries):
                button = QPushButton(label)
                button.setMinimumHeight(28)
                class_id = TILE_CLASSES.index(class_name)
                button.setCheckable(True)
                self.class_buttons.addButton(button, class_id)
                grid.addWidget(button, index // 3, index % 3)
            panel.setLayout(grid)

            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setWidget(panel)
            tabs.addTab(scroll, title)

        return tabs

    def numbered_tile_entries(self, suit: str) -> list[tuple[str, str]]:
        entries = [(f"{suit}_{number}", str(number)) for number in range(1, 10)]
        entries.insert(5, (f"{suit}_5_red", "5R"))
        return entries

    def _install_shortcuts(self) -> None:
        self.rebuild_shortcuts()

    def rebuild_shortcuts(self) -> None:
        for shortcut in self.shortcuts:
            shortcut.setEnabled(False)
            shortcut.deleteLater()

        self.shortcuts = []
        for action_id, (_label, default, callback) in self.shortcut_actions().items():
            edit = self.shortcut_edits.get(action_id)
            sequence = edit.keySequence() if edit is not None else QKeySequence(default)
            if sequence.isEmpty():
                continue

            shortcut = QShortcut(sequence, self)
            shortcut.activated.connect(callback)
            self.shortcuts.append(shortcut)

        self.save_shortcut_config()

    def load_shortcut_config(self) -> dict[str, str]:
        if not SHORTCUTS_CONFIG_PATH.exists():
            return {}

        try:
            data = json.loads(SHORTCUTS_CONFIG_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}

        return {key: str(value) for key, value in data.items() if isinstance(value, str)}

    def save_shortcut_config(self) -> None:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        data = {
            action_id: edit.keySequence().toString()
            for action_id, edit in self.shortcut_edits.items()
        }
        SHORTCUTS_CONFIG_PATH.write_text(
            json.dumps(data, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    def annotation_mode(self) -> str:
        return str(self.helper_mode_combo.currentData() or "manual")

    def annotation_mode_changed(self) -> None:
        mode = self.annotation_mode()
        if mode == "manual":
            self.helper_boxes = []
            self.selected_annotation_index = None
            self.pending_selected_suit = None
            self.redraw_annotations()
            self.statusBar().showMessage("Modo manual: arraste para criar boxes.")
            return

        message = (
            "Box-helper: clique em Detectar boxes uma vez; depois escolha a classe e clique na sugestao."
            if mode == "box_helper"
            else "Predizer e revisar: clique em Detectar boxes uma vez; depois corrija manualmente."
        )
        self.statusBar().showMessage(message)

    def available_model_paths(self) -> list[Path]:
        paths: list[Path] = []
        if RUNS_DIR.exists():
            paths.extend(RUNS_DIR.glob("*/weights/best.pt"))
        paths.extend(
            path
            for path in PROJECT_ROOT.glob("*.pt")
            if path.is_file() and path.name.lower().startswith(("yolo", "rtdetr"))
        )
        return sorted(
            set(paths),
            key=lambda path: (0 if "runs" in path.parts else 1, -path.stat().st_mtime),
        )

    def refresh_model_list(self) -> None:
        current_path = self.selected_annotation_model_path()
        self.model_combo.blockSignals(True)
        self.model_combo.clear()

        paths = self.available_model_paths()
        for path in paths:
            label = path.relative_to(PROJECT_ROOT).as_posix()
            self.model_combo.addItem(label, str(path))

        if not paths:
            self.model_combo.addItem("Nenhum modelo .pt encontrado", "")

        if current_path is not None:
            index = self.model_combo.findData(str(current_path))
            if index >= 0:
                self.model_combo.setCurrentIndex(index)

        self.model_combo.blockSignals(False)
        self.unload_annotation_model()

    def selected_annotation_model_path(self) -> Path | None:
        data = self.model_combo.currentData()
        return Path(data) if data else None

    def unload_annotation_model(self, *_args) -> None:
        self.annotation_model = None
        self.annotation_model_path = None

    def prediction_device(self) -> str:
        try:
            import torch

            return "0" if torch.cuda.is_available() else "cpu"
        except Exception:
            return "cpu"

    def load_annotation_model(self):
        model_path = self.selected_annotation_model_path()
        if model_path is None:
            self.statusBar().showMessage("Nenhum modelo auxiliar selecionado.")
            return None

        if self.annotation_model is not None and self.annotation_model_path == model_path:
            return self.annotation_model

        try:
            from ultralytics import RTDETR, YOLO

            is_rtdetr = any("rtdetr" in part.lower() for part in model_path.parts)
            self.annotation_model = RTDETR(str(model_path)) if is_rtdetr else YOLO(str(model_path))
            self.annotation_model_path = model_path
            self.statusBar().showMessage(f"Modelo auxiliar carregado: {model_path.name}")
            return self.annotation_model
        except Exception as error:
            self.statusBar().showMessage(f"Falha ao carregar modelo auxiliar: {type(error).__name__}: {error}")
            self.annotation_model = None
            self.annotation_model_path = None
            return None

    def detect_boxes_for_current_mode(self) -> None:
        if self.current_image_path is None or self.current_pixmap.isNull():
            self.statusBar().showMessage("Nenhuma imagem carregada para detectar.")
            return

        mode = self.annotation_mode()
        if mode == "manual":
            self.statusBar().showMessage("Troque para Box-helper ou Predizer e revisar para usar o modelo.")
            return

        model = self.load_annotation_model()
        if model is None:
            return

        predict_conf = self.conf_input.value()
        if mode == "predict_review":
            predict_conf = min(0.05, predict_conf)

        try:
            image_size = max(self.current_pixmap.width(), self.current_pixmap.height())
            image_size = ((image_size + 31) // 32) * 32
            result = model.predict(
                source=str(self.current_image_path),
                imgsz=image_size,
                conf=predict_conf,
                device=self.prediction_device(),
                verbose=False,
            )[0]
        except Exception as error:
            self.statusBar().showMessage(f"Falha na deteccao auxiliar: {type(error).__name__}: {error}")
            return

        helper_boxes = self.helper_boxes_from_result(result)
        self.selected_annotation_index = None
        self.pending_selected_suit = None
        if mode == "predict_review":
            self.annotations = self.review_annotations_from_helper_boxes(helper_boxes)
            self.helper_boxes = []
            empty_count = sum(1 for annotation in self.annotations if annotation.class_id is None)
            self.redraw_annotations()
            self.statusBar().showMessage(
                f"Predicao aplicada: {len(self.annotations)} boxes, {empty_count} vazios abaixo do threshold. Clique nos vazios e use atalhos."
            )
        else:
            self.helper_boxes = helper_boxes
            self.redraw_annotations()
            self.statusBar().showMessage(
                f"Box-helper: {len(self.helper_boxes)} sugestoes. Escolha a classe e clique na peca."
            )

    def helper_boxes_from_result(self, result) -> list[HelperBox]:
        names = getattr(result, "names", {}) or {}
        helper_boxes: list[HelperBox] = []
        for box in result.boxes:
            x1, y1, x2, y2 = [float(value) for value in box.xyxy[0]]
            raw_class_id = int(box.cls[0])
            raw_name = str(names.get(raw_class_id, raw_class_id))
            if raw_name in TILE_CLASSES:
                class_id = TILE_CLASSES.index(raw_name)
            elif 0 <= raw_class_id < len(TILE_CLASSES):
                class_id = raw_class_id
            else:
                continue

            rect = QRectF(x1, y1, max(1.0, x2 - x1), max(1.0, y2 - y1)).normalized()
            helper_boxes.append(HelperBox(rect=rect, class_id=class_id, confidence=float(box.conf[0])))

        return sorted(helper_boxes, key=lambda item: (item.rect.top(), item.rect.left()))

    def review_annotations_from_helper_boxes(self, helper_boxes: list[HelperBox]) -> list[Annotation]:
        threshold = self.conf_input.value()
        normal_boxes = [
            Annotation(helper.class_id, QRectF(helper.rect))
            for helper in helper_boxes
            if helper.class_id is not None and helper.confidence >= threshold
        ]
        empty_boxes = [
            Annotation(None, QRectF(helper.rect))
            for helper in helper_boxes
            if helper.confidence < threshold
        ]

        filtered_empty_boxes = [
            empty
            for empty in empty_boxes
            if not any(self.is_redundant_empty_box(empty.rect, normal.rect) for normal in normal_boxes)
        ]
        return sorted(normal_boxes + filtered_empty_boxes, key=lambda item: (item.rect.top(), item.rect.left()))

    def is_redundant_empty_box(self, empty_rect: QRectF, normal_rect: QRectF) -> bool:
        empty = empty_rect.normalized()
        normal = normal_rect.normalized()
        intersection = empty.intersected(normal)
        if intersection.isNull():
            return False

        empty_area = max(1.0, empty.width() * empty.height())
        normal_area = max(1.0, normal.width() * normal.height())
        intersection_area = intersection.width() * intersection.height()
        union_area = empty_area + normal_area - intersection_area

        overlap_on_empty = intersection_area / empty_area
        overlap_on_normal = intersection_area / normal_area
        iou = intersection_area / max(1.0, union_area)
        return overlap_on_empty >= 0.55 or overlap_on_normal >= 0.55 or iou >= 0.35

    def load_image_by_index(self, index: int) -> None:
        if index < 0 or index >= len(self.images):
            return

        self.current_index = index
        self.current_image_path = self.images[index]
        self.current_pixmap = QPixmap(str(self.current_image_path))
        self.annotation_items = []
        self.annotation_labels = []
        self.helper_boxes = []
        self.helper_items = []
        self.helper_labels = []
        self.selected_annotation_index = None
        self.pending_selected_suit = None
        self.view.load_pixmap(self.current_pixmap)
        self.annotations = self.load_existing_annotations(self.current_image_path)
        self.redraw_annotations()
        self.statusBar().showMessage(f"{self.current_image_path.name} carregada.")

    def load_existing_annotations(self, image_path: Path) -> list[Annotation]:
        for split, radio in self.split_radios():
            label_path = DATASET_DIR / "labels" / split / f"{image_path.stem}.txt"
            if not label_path.exists():
                continue

            radio.setChecked(True)
            return self.read_yolo_labels(label_path)

        self.train_radio.setChecked(True)
        return []

    def read_yolo_labels(self, label_path: Path) -> list[Annotation]:
        width = self.current_pixmap.width()
        height = self.current_pixmap.height()
        annotations = []
        for line in label_path.read_text(encoding="utf-8").splitlines():
            parts = line.split()
            if len(parts) != 5:
                continue

            class_id = int(parts[0])
            if class_id < 0 or class_id >= len(TILE_CLASSES):
                continue

            x_center, y_center, box_width, box_height = map(float, parts[1:])
            rect_width = box_width * width
            rect_height = box_height * height
            left = x_center * width - rect_width / 2
            top = y_center * height - rect_height / 2
            annotations.append(
                Annotation(class_id, QRectF(left, top, rect_width, rect_height))
            )

        return annotations

    def selected_class_id(self) -> int:
        return self.class_combo.currentIndex()

    def select_class(self, class_id: int) -> None:
        if self.class_combo.currentIndex() == class_id:
            self.class_changed(class_id)
        else:
            self.class_combo.setCurrentIndex(class_id)

    def class_changed(self, class_id: int) -> None:
        self.sync_class_buttons(class_id)
        if self.selected_annotation_index is not None:
            self.assign_selected_annotation_class(class_id)

    def sync_class_buttons(self, class_id: int) -> None:
        button = self.class_buttons.button(class_id)
        if button is not None:
            button.setChecked(True)

    def select_suit(self, suit: str) -> None:
        tab_indexes = {"man": 0, "pin": 1, "sou": 2}
        self.class_tabs.setCurrentIndex(tab_indexes[suit])
        if self.selected_empty_annotation_index() is not None:
            self.pending_selected_suit = suit
            self.statusBar().showMessage(
                f"Naipe {suit} selecionado para o box vazio. Aperte 1-9 ou R para concluir."
            )
            return

        current = TILE_CLASSES[self.selected_class_id()]
        number = current.rsplit("_", 1)[-1] if current.rsplit("_", 1)[-1].isdigit() else "1"
        self.select_class(TILE_CLASSES.index(f"{suit}_{number}"))

    def select_honors(self) -> None:
        self.class_tabs.setCurrentIndex(3)
        self.pending_selected_suit = None
        if self.selected_empty_annotation_index() is not None:
            self.statusBar().showMessage("Honras abertas. Use o atalho da honra para aplicar direto no box vazio.")
            return

        if not TILE_CLASSES[self.selected_class_id()].startswith(("wind_", "dragon_")):
            self.select_class(TILE_CLASSES.index("wind_east"))

    def select_honor(self, class_name: str) -> None:
        self.pending_selected_suit = None
        self.class_tabs.setCurrentIndex(3)
        self.select_class(TILE_CLASSES.index(class_name))

    def select_number(self, number: int) -> None:
        suit = self.pending_selected_suit
        if suit is None:
            current = TILE_CLASSES[self.selected_class_id()]
            suit = current.split("_", 1)[0]
            if suit not in {"man", "pin", "sou"}:
                suit = "man"
        self.pending_selected_suit = None
        self.select_class(TILE_CLASSES.index(f"{suit}_{number}"))

    def select_red_five(self) -> None:
        suit = self.pending_selected_suit
        if suit is None:
            current = TILE_CLASSES[self.selected_class_id()]
            suit = current.split("_", 1)[0]
            if suit not in {"man", "pin", "sou"}:
                suit = "man"
        self.pending_selected_suit = None
        self.select_class(TILE_CLASSES.index(f"{suit}_5_red"))

    def selected_empty_annotation_index(self) -> int | None:
        index = self.selected_annotation_index
        if index is None or not 0 <= index < len(self.annotations):
            return None
        return index if self.annotations[index].class_id is None else None

    def add_annotation(self, rect: QRectF) -> None:
        self.annotations.append(Annotation(self.selected_class_id(), rect))
        self.selected_annotation_index = None
        self.pending_selected_suit = None
        self.redraw_annotations()

    def handle_image_click(self, pos: QPointF) -> bool:
        annotation_index = self.annotation_index_at(pos)
        if annotation_index is not None and self.annotations[annotation_index].class_id is None:
            self.select_annotation_index(annotation_index)
            return True

        if self.annotation_mode() not in {"box_helper", "predict_review"}:
            self.clear_annotation_selection()
            return False

        if annotation_index is not None:
            # A single click on an existing/predicted annotation behaves like
            # manual mode: it can start a drag, but it does not select the box.
            self.clear_annotation_selection()
            return False

        helper_index = self.helper_box_index_at(pos)
        if helper_index is None:
            self.clear_annotation_selection()
            return False

        helper = self.helper_boxes.pop(helper_index)
        class_id = self.selected_class_id()
        if self.annotation_mode() == "predict_review" and helper.class_id is not None:
            class_id = helper.class_id
        self.annotations.append(Annotation(class_id, QRectF(helper.rect)))
        self.selected_annotation_index = None
        self.pending_selected_suit = None
        self.redraw_annotations()
        self.statusBar().showMessage(
            f"Box anotado como {TILE_CLASSES[class_id]}. Use atalhos para corrigir se precisar."
        )
        return True

    def handle_image_double_click(self, pos: QPointF) -> bool:
        annotation_index = self.annotation_index_at(pos)
        if annotation_index is None:
            return False
        self.select_annotation_index(annotation_index)
        return True

    def clear_annotation_selection(self) -> None:
        if self.selected_annotation_index is None:
            return
        self.selected_annotation_index = None
        self.pending_selected_suit = None
        self.view.scene().clearSelection()
        self.redraw_annotations()

    def annotation_index_at(self, pos: QPointF) -> int | None:
        for index in range(len(self.annotations) - 1, -1, -1):
            if self.annotations[index].rect.contains(pos):
                return index
        return None

    def helper_box_index_at(self, pos: QPointF) -> int | None:
        for index in range(len(self.helper_boxes) - 1, -1, -1):
            if self.helper_boxes[index].rect.contains(pos):
                return index
        return None

    def select_annotation_index(self, index: int) -> None:
        if not 0 <= index < len(self.annotations):
            self.selected_annotation_index = None
            self.pending_selected_suit = None
            return
        self.selected_annotation_index = index
        self.pending_selected_suit = None
        self.view.scene().clearSelection()
        if 0 <= index < len(self.annotation_items):
            self.annotation_items[index].setSelected(True)
        class_id = self.annotations[index].class_id
        if class_id is None:
            self.redraw_annotations()
            self.statusBar().showMessage(
                "Caixa vazia selecionada. Use os atalhos para classificar; arraste a borda inferior direita para redimensionar."
            )
            return
        if self.class_combo.currentIndex() != class_id:
            self.class_combo.blockSignals(True)
            self.class_combo.setCurrentIndex(class_id)
            self.class_combo.blockSignals(False)
            self.sync_class_buttons(class_id)
        self.statusBar().showMessage(
            f"Selecionada: {TILE_CLASSES[class_id]}. Use atalhos para mudar a classe."
        )

    def assign_selected_annotation_class(self, class_id: int) -> None:
        index = self.selected_annotation_index
        if index is None or not 0 <= index < len(self.annotations):
            return
        was_empty = self.annotations[index].class_id is None
        self.annotations[index].class_id = class_id
        self.pending_selected_suit = None
        if was_empty:
            self.selected_annotation_index = None
        self.redraw_annotations()
        self.statusBar().showMessage(f"Box selecionado alterado para {TILE_CLASSES[class_id]}.")

    def redraw_annotations(self) -> None:
        for item in self.annotation_items + self.annotation_labels + self.helper_items + self.helper_labels:
            try:
                if item.scene() is self.view.scene():
                    self.view.scene().removeItem(item)
            except RuntimeError:
                pass

        self.annotation_items = []
        self.annotation_labels = []
        self.helper_items = []
        self.helper_labels = []
        for index, annotation in enumerate(self.annotations):
            is_selected = index == self.selected_annotation_index
            is_empty = annotation.class_id is None
            color = QColor("#38BDF8") if is_selected else QColor("#FBBF24" if is_empty else "#F97316")
            rect_item = AnnotationRectItem(
                index,
                annotation,
                is_selected,
                editable=is_empty and is_selected,
                changed_callback=self.update_annotation_rect,
            )
            rect_item.setPen(QPen(color, 3 if is_selected else 2, Qt.PenStyle.DashLine if is_empty else Qt.PenStyle.SolidLine))
            rect_item.setBrush(QBrush(QColor(color.red(), color.green(), color.blue(), 24 if is_empty else 34)))
            rect_item.setZValue(20)
            self.view.scene().addItem(rect_item)

            text = QGraphicsTextItem(TILE_CLASSES[annotation.class_id] if annotation.class_id is not None else "???")
            text.setDefaultTextColor(QColor("#FFFFFF"))
            text.setPos(annotation.rect.left(), max(0, annotation.rect.top() - 24))
            text.setData(0, index)
            text.setZValue(21)
            self.view.scene().addItem(text)

            self.annotation_items.append(rect_item)
            self.annotation_labels.append(text)

        for index, helper in enumerate(self.helper_boxes):
            rect_item = self.view.scene().addRect(
                helper.rect,
                QPen(QColor("#22D3EE"), 2, Qt.PenStyle.DashLine),
                QBrush(QColor(34, 211, 238, 18)),
            )
            rect_item.setData(1, index)
            rect_item.setZValue(10)

            label_name = TILE_CLASSES[helper.class_id] if helper.class_id is not None else "box"
            text = QGraphicsTextItem(f"{label_name} {helper.confidence:.2f}")
            text.setDefaultTextColor(QColor("#67E8F9"))
            text.setPos(helper.rect.left(), max(0, helper.rect.top() - 22))
            text.setData(1, index)
            text.setZValue(11)
            self.view.scene().addItem(text)

            self.helper_items.append(rect_item)
            self.helper_labels.append(text)

        helper_part = f" | {len(self.helper_boxes)} sugestoes" if self.helper_boxes else ""
        self.statusBar().showMessage(f"{len(self.annotations)} anotacoes nesta imagem{helper_part}.")

    def update_annotation_rect(self, index: int, rect: QRectF) -> None:
        if not 0 <= index < len(self.annotations):
            return
        image_rect = QRectF(0, 0, self.current_pixmap.width(), self.current_pixmap.height())
        rect = rect.normalized()
        width = min(max(6.0, rect.width()), image_rect.width())
        height = min(max(6.0, rect.height()), image_rect.height())
        left = min(max(image_rect.left(), rect.left()), max(image_rect.left(), image_rect.right() - width))
        top = min(max(image_rect.top(), rect.top()), max(image_rect.top(), image_rect.bottom() - height))
        self.annotations[index].rect = QRectF(left, top, width, height)

    def delete_selected(self) -> None:
        selected_indexes = sorted(
            {
                int(item.data(0))
                for item in self.view.scene().selectedItems()
                if item.data(0) is not None
            },
            reverse=True,
        )
        for index in selected_indexes:
            if 0 <= index < len(self.annotations):
                self.annotations.pop(index)

        if selected_indexes:
            self.selected_annotation_index = None
            self.pending_selected_suit = None
            self.redraw_annotations()

    def undo_last(self) -> None:
        if self.annotations:
            self.annotations.pop()
            self.selected_annotation_index = None
            self.pending_selected_suit = None
            self.redraw_annotations()

    def save_current(self) -> None:
        if self.current_image_path is None or self.current_pixmap.isNull():
            return

        split = self.selected_split()
        image_dir = DATASET_DIR / "images" / split
        label_dir = DATASET_DIR / "labels" / split
        image_dir.mkdir(parents=True, exist_ok=True)
        label_dir.mkdir(parents=True, exist_ok=True)

        image_path = image_dir / self.current_image_path.name
        label_path = label_dir / f"{self.current_image_path.stem}.txt"
        shutil.copy2(self.current_image_path, image_path)
        label_path.write_text(self.yolo_label_text(), encoding="utf-8")
        self.remove_from_other_split(split)
        self.refresh_image_statuses()
        self.refresh_train_summary()
        self.statusBar().showMessage(f"Salvo em {split}: {image_path.name}")

    def remove_from_other_split(self, current_split: str) -> None:
        if self.current_image_path is None:
            return

        for split in DATASET_SPLITS:
            if split == current_split:
                continue
            other_image = DATASET_DIR / "images" / split / self.current_image_path.name
            other_label = DATASET_DIR / "labels" / split / f"{self.current_image_path.stem}.txt"
            other_image.unlink(missing_ok=True)
            other_label.unlink(missing_ok=True)

    def delete_image(self, image_path: Path) -> None:
        if image_path not in self.images:
            return

        deleted_current = image_path == self.current_image_path
        old_index = self.images.index(image_path)
        image_path.unlink(missing_ok=True)
        for split in DATASET_SPLITS:
            (DATASET_DIR / "images" / split / image_path.name).unlink(missing_ok=True)
            (DATASET_DIR / "labels" / split / f"{image_path.stem}.txt").unlink(missing_ok=True)

        self.images = self._raw_images()
        self.refresh_train_summary()
        if not self.images:
            self.current_index = 0
            self.current_image_path = None
            self.current_pixmap = QPixmap()
            self.annotations = []
            self.helper_boxes = []
            self.pending_selected_suit = None
            self.view.scene().clear()
            self.populate_image_list()
            self.statusBar().showMessage("Imagem deletada. Nenhuma imagem restante.")
            return

        next_index = min(old_index, len(self.images) - 1)
        if not deleted_current and self.current_image_path in self.images:
            next_index = self.images.index(self.current_image_path)
        self.populate_image_list(next_index)
        self.statusBar().showMessage(f"Imagem deletada: {image_path.name}")

    def split_radios(self) -> tuple[tuple[str, QRadioButton], ...]:
        return (
            ("train", self.train_radio),
            ("val", self.val_radio),
            ("test", self.test_radio),
        )

    def selected_split(self) -> str:
        for split, radio in self.split_radios():
            if radio.isChecked():
                return split
        return "train"

    def yolo_label_text(self) -> str:
        width = self.current_pixmap.width()
        height = self.current_pixmap.height()
        lines = []
        for annotation in self.annotations:
            if annotation.class_id is None:
                continue
            rect = annotation.rect.normalized()
            x_center = (rect.left() + rect.width() / 2) / width
            y_center = (rect.top() + rect.height() / 2) / height
            box_width = rect.width() / width
            box_height = rect.height() / height
            lines.append(
                f"{annotation.class_id} {x_center:.6f} {y_center:.6f} "
                f"{box_width:.6f} {box_height:.6f}"
            )

        return "\n".join(lines) + ("\n" if lines else "")

    def next_image(self) -> None:
        if not self.images:
            return

        next_index = min(self.current_index + 1, len(self.images) - 1)
        self.image_list.setCurrentRow(next_index)

    def previous_image(self) -> None:
        if not self.images:
            return

        previous_index = max(self.current_index - 1, 0)
        self.image_list.setCurrentRow(previous_index)
