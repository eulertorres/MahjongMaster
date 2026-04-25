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
CONFIG_DIR = PROJECT_ROOT / "config"
SHORTCUTS_CONFIG_PATH = CONFIG_DIR / "shortcuts.json"
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg"}


@dataclass
class Annotation:
    class_id: int
    rect: QRectF


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
        self.annotation_items: list[QGraphicsRectItem] = []
        self.annotation_labels: list[QGraphicsTextItem] = []
        self.shortcuts: list[QShortcut] = []
        self.shortcut_edits: dict[str, QKeySequenceEdit] = {}
        self.shortcut_config = self.load_shortcut_config()

        self.image_list = QListWidget()
        self.image_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.image_list.currentRowChanged.connect(self.load_image_by_index)
        self.image_list.setMinimumWidth(280)
        for path in self.images:
            self.image_list.addItem(QListWidgetItem(path.name))
        self.refresh_image_statuses()

        self.train_summary_list = QListWidget()
        self.train_summary_list.setMinimumHeight(180)
        self.refresh_train_summary()

        self.view = AnnotationView()
        self.view.box_created.connect(self.add_annotation)
        self.view.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        self.class_combo = QComboBox()
        self.class_combo.addItems(TILE_CLASSES)
        self.class_combo.currentIndexChanged.connect(self.sync_class_buttons)

        self.class_buttons = QButtonGroup(self)
        self.class_buttons.idClicked.connect(self.select_class)
        self.class_tabs = self._build_class_tabs()

        self.train_radio = QRadioButton("train")
        self.val_radio = QRadioButton("val")
        self.train_radio.setChecked(True)

        self.save_button = QPushButton("Salvar")
        self.save_button.clicked.connect(self.save_current)
        self.next_button = QPushButton("Proxima")
        self.next_button.clicked.connect(self.next_image)
        self.delete_button = QPushButton("Remover selecionada")
        self.delete_button.clicked.connect(self.delete_selected)
        self.undo_button = QPushButton("Desfazer ultima")
        self.undo_button.clicked.connect(self.undo_last)
        self.shortcuts_panel = self._build_shortcuts_panel()

        right = QVBoxLayout()
        right.addWidget(QLabel("Classe ativa"))
        right.addWidget(self.class_combo)
        right.addWidget(self.class_tabs, stretch=1)
        right.addWidget(self.shortcuts_panel)
        right.addWidget(QLabel("Destino"))
        right.addWidget(self.train_radio)
        right.addWidget(self.val_radio)
        right.addWidget(self.save_button)
        right.addWidget(self.next_button)
        right.addWidget(self.delete_button)
        right.addWidget(self.undo_button)

        left = QVBoxLayout()
        left.addWidget(QLabel("Screenshots"))
        left.addWidget(self.image_list, stretch=3)
        left.addWidget(QLabel("Pecas no treino"))
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

        for action_id, (label, default, _callback) in self.shortcut_actions().items():
            edit = QKeySequenceEdit(QKeySequence(self.shortcut_config.get(action_id, default)))
            edit.editingFinished.connect(self.rebuild_shortcuts)
            self.shortcut_edits[action_id] = edit
            form.addRow(label, edit)

        group.setLayout(form)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMaximumHeight(260)
        scroll.setWidget(group)
        return scroll

    def _raw_images(self) -> list[Path]:
        RAW_DATASET_DIR.mkdir(parents=True, exist_ok=True)
        return sorted(
            path
            for path in RAW_DATASET_DIR.iterdir()
            if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
        )

    def split_for_image(self, image_path: Path) -> str | None:
        for split in ("train", "val"):
            label_path = DATASET_DIR / "labels" / split / f"{image_path.stem}.txt"
            image_copy = DATASET_DIR / "images" / split / image_path.name
            if label_path.exists() or image_copy.exists():
                return split

        return None

    def refresh_image_statuses(self) -> None:
        colors = {
            "train": QColor("#15803D"),
            "val": QColor("#2563EB"),
        }
        labels = {
            "train": "salva para treino",
            "val": "salva para validacao",
        }

        for index, image_path in enumerate(self.images):
            item = self.image_list.item(index)
            split = self.split_for_image(image_path)
            if split is None:
                item.setForeground(QBrush(QColor("#E5E7EB")))
                item.setToolTip("Ainda nao salva")
                item.setText(image_path.name)
                continue

            item.setForeground(QBrush(colors[split]))
            item.setToolTip(labels[split])
            item.setText(f"{image_path.name}  [{split}]")

    def refresh_train_summary(self) -> None:
        if not hasattr(self, "train_summary_list"):
            return

        counts = [0 for _ in TILE_CLASSES]
        labels_dir = DATASET_DIR / "labels" / "train"
        if labels_dir.exists():
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
        total = sum(counts)
        self.train_summary_list.addItem(f"Total: {total}")

        for class_id, count in enumerate(counts):
            if count <= 0:
                continue
            self.train_summary_list.addItem(f"{TILE_CLASSES[class_id]}: {count}")

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
                ("face_down", "Face down"),
            ],
        }

        for title, entries in groups.items():
            panel = QWidget()
            grid = QGridLayout()
            for index, (class_name, label) in enumerate(entries):
                button = QPushButton(label)
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

    def load_image_by_index(self, index: int) -> None:
        if index < 0 or index >= len(self.images):
            return

        self.current_index = index
        self.current_image_path = self.images[index]
        self.current_pixmap = QPixmap(str(self.current_image_path))
        self.annotation_items = []
        self.annotation_labels = []
        self.view.load_pixmap(self.current_pixmap)
        self.annotations = self.load_existing_annotations(self.current_image_path)
        self.redraw_annotations()
        self.statusBar().showMessage(f"{self.current_image_path.name} carregada.")

    def load_existing_annotations(self, image_path: Path) -> list[Annotation]:
        for split, radio in (("train", self.train_radio), ("val", self.val_radio)):
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
        self.class_combo.setCurrentIndex(class_id)

    def sync_class_buttons(self, class_id: int) -> None:
        button = self.class_buttons.button(class_id)
        if button is not None:
            button.setChecked(True)

    def select_suit(self, suit: str) -> None:
        tab_indexes = {"man": 0, "pin": 1, "sou": 2}
        self.class_tabs.setCurrentIndex(tab_indexes[suit])
        current = TILE_CLASSES[self.selected_class_id()]
        number = current.rsplit("_", 1)[-1] if current.rsplit("_", 1)[-1].isdigit() else "1"
        self.select_class(TILE_CLASSES.index(f"{suit}_{number}"))

    def select_honors(self) -> None:
        self.class_tabs.setCurrentIndex(3)
        if not TILE_CLASSES[self.selected_class_id()].startswith(("wind_", "dragon_")):
            self.select_class(TILE_CLASSES.index("wind_east"))

    def select_honor(self, class_name: str) -> None:
        self.class_tabs.setCurrentIndex(3)
        self.select_class(TILE_CLASSES.index(class_name))

    def select_number(self, number: int) -> None:
        current = TILE_CLASSES[self.selected_class_id()]
        suit = current.split("_", 1)[0]
        if suit not in {"man", "pin", "sou"}:
            suit = "man"
        self.select_class(TILE_CLASSES.index(f"{suit}_{number}"))

    def select_red_five(self) -> None:
        current = TILE_CLASSES[self.selected_class_id()]
        suit = current.split("_", 1)[0]
        if suit not in {"man", "pin", "sou"}:
            suit = "man"
        self.select_class(TILE_CLASSES.index(f"{suit}_5_red"))

    def add_annotation(self, rect: QRectF) -> None:
        self.annotations.append(Annotation(self.selected_class_id(), rect))
        self.redraw_annotations()

    def redraw_annotations(self) -> None:
        for item in self.annotation_items + self.annotation_labels:
            try:
                if item.scene() is self.view.scene():
                    self.view.scene().removeItem(item)
            except RuntimeError:
                pass

        self.annotation_items = []
        self.annotation_labels = []
        for index, annotation in enumerate(self.annotations):
            rect_item = self.view.scene().addRect(
                annotation.rect,
                QPen(QColor("#F97316"), 2),
                QBrush(QColor(249, 115, 22, 32)),
            )
            rect_item.setFlag(QGraphicsRectItem.GraphicsItemFlag.ItemIsSelectable, True)
            rect_item.setData(0, index)

            text = QGraphicsTextItem(TILE_CLASSES[annotation.class_id])
            text.setDefaultTextColor(QColor("#FFFFFF"))
            text.setPos(annotation.rect.left(), max(0, annotation.rect.top() - 24))
            text.setData(0, index)
            self.view.scene().addItem(text)

            self.annotation_items.append(rect_item)
            self.annotation_labels.append(text)

        self.statusBar().showMessage(f"{len(self.annotations)} anotacoes nesta imagem.")

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
            self.redraw_annotations()

    def undo_last(self) -> None:
        if self.annotations:
            self.annotations.pop()
            self.redraw_annotations()

    def save_current(self) -> None:
        if self.current_image_path is None or self.current_pixmap.isNull():
            return

        split = "val" if self.val_radio.isChecked() else "train"
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

        other_split = "val" if current_split == "train" else "train"
        other_image = DATASET_DIR / "images" / other_split / self.current_image_path.name
        other_label = DATASET_DIR / "labels" / other_split / f"{self.current_image_path.stem}.txt"
        other_image.unlink(missing_ok=True)
        other_label.unlink(missing_ok=True)

    def yolo_label_text(self) -> str:
        width = self.current_pixmap.width()
        height = self.current_pixmap.height()
        lines = []
        for annotation in self.annotations:
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
