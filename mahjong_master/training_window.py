from __future__ import annotations

import csv
import re
import shutil
import sys
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from PyQt6.QtCore import QPointF, QProcess, QRectF, Qt, QTimer
from PyQt6.QtGui import QBrush, QColor, QFont, QPainter, QPen, QPixmap, QPolygonF
from PyQt6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from mahjong_master.tile_classes import TILE_CLASSES


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RUNS_DIR = PROJECT_ROOT / "runs" / "detect"
TRAIN_SCRIPT = PROJECT_ROOT / "scripts" / "train_yolo.py"
DATA_YAML = PROJECT_ROOT / "data" / "mahjong_soul.yaml"
COLAB_DATASET_ZIP_NAME = "mahjongmaster_colab_dataset.zip"
COLAB_DATASET_ZIP = PROJECT_ROOT / COLAB_DATASET_ZIP_NAME
ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
PACKAGE_EXTENSIONS = {".yaml", ".yml", ".png", ".jpg", ".jpeg", ".txt"}
RUN_COLORS = [
    "#2563EB",
    "#DC2626",
    "#16A34A",
    "#7C3AED",
    "#EA580C",
    "#0891B2",
    "#BE123C",
    "#4D7C0F",
    "#9333EA",
]
SUMMARY_COLUMNS = [
    "train/box_loss",
    "train/cls_loss",
    "train/dfl_loss",
    "val/box_loss",
    "val/cls_loss",
    "val/dfl_loss",
    "metrics/mAP50(B)",
    "metrics/precision(B)",
    "metrics/recall(B)",
]


def float_value(value: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def read_results_rows(run_dir: Path) -> list[dict[str, str]]:
    results_path = run_dir / "results.csv"
    if not results_path.exists():
        return []
    try:
        with results_path.open("r", encoding="utf-8", newline="") as file:
            return list(csv.DictReader(file))
    except OSError:
        return []


def package_file_allowed(path: Path) -> bool:
    return path.suffix.lower() in PACKAGE_EXTENSIONS and "raw" not in path.parts


def add_package_tree(zip_file: ZipFile, directory: Path) -> int:
    if not directory.exists():
        return 0

    count = 0
    for path in directory.rglob("*"):
        if not path.is_file() or not package_file_allowed(path):
            continue
        zip_file.write(path, path.relative_to(PROJECT_ROOT))
        count += 1
    return count


def create_dataset_zip(output_path: Path = COLAB_DATASET_ZIP) -> int:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(output_path, "w", ZIP_DEFLATED) as zip_file:
        file_count = 0
        file_count += add_package_tree(zip_file, PROJECT_ROOT / "data")
        file_count += add_package_tree(zip_file, PROJECT_ROOT / "dataset")
    return file_count


def loss_train_series(rows: list[dict[str, str]]):
    epochs = [int(float(row["epoch"])) for row in rows]
    return [
        ("train/box_loss", "#2563EB", list(zip(epochs, [float_value(row.get("train/box_loss", "")) for row in rows])), "erro da caixa"),
        ("train/cls_loss", "#DC2626", list(zip(epochs, [float_value(row.get("train/cls_loss", "")) for row in rows])), "erro da classe"),
        ("train/dfl_loss", "#16A34A", list(zip(epochs, [float_value(row.get("train/dfl_loss", "")) for row in rows])), "erro das bordas"),
    ]


def loss_val_series(rows: list[dict[str, str]]):
    epochs = [int(float(row["epoch"])) for row in rows]
    return [
        ("val/box_loss", "#2563EB", list(zip(epochs, [float_value(row.get("val/box_loss", "")) for row in rows])), "caixa em imagens novas"),
        ("val/cls_loss", "#DC2626", list(zip(epochs, [float_value(row.get("val/cls_loss", "")) for row in rows])), "classe em imagens novas"),
        ("val/dfl_loss", "#16A34A", list(zip(epochs, [float_value(row.get("val/dfl_loss", "")) for row in rows])), "bordas em imagens novas"),
    ]


def metric_series(rows: list[dict[str, str]]):
    epochs = [int(float(row["epoch"])) for row in rows]
    return [
        ("metrics/mAP50(B)", "#7C3AED", list(zip(epochs, [float_value(row.get("metrics/mAP50(B)", "")) for row in rows])), "qualidade geral"),
        ("metrics/precision(B)", "#2563EB", list(zip(epochs, [float_value(row.get("metrics/precision(B)", "")) for row in rows])), "menos falsos positivos"),
        ("metrics/recall(B)", "#16A34A", list(zip(epochs, [float_value(row.get("metrics/recall(B)", "")) for row in rows])), "menos pecas perdidas"),
    ]


class TrainingPlot(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setMinimumSize(520, 240)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.title = "Aguardando resultados do treino"
        self.y_label = "valor"
        self.x_label = "Epoca"
        self.y_range: tuple[float, float] | None = None
        self.series: list[tuple[str, QColor, list[tuple[int, float]], str]] = []

    def set_data(
        self,
        title: str,
        y_label: str,
        series: list[tuple[str, str, list[tuple[int, float]], str]],
        y_range: tuple[float, float] | None = None,
    ) -> None:
        self.title = title
        self.y_label = y_label
        self.series = [
            (label, QColor(color), points, description)
            for label, color, points, description in series
        ]
        self.y_range = y_range
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#0B1220"))

        margins = {"left": 72, "top": 42, "right": 28, "bottom": 54}
        plot = QRectF(
            margins["left"],
            margins["top"],
            self.width() - margins["left"] - margins["right"],
            self.height() - margins["top"] - margins["bottom"],
        )

        painter.setPen(QPen(QColor("#F9FAFB"), 1))
        painter.setFont(QFont("Segoe UI", 13, QFont.Weight.Bold))
        painter.drawText(QRectF(0, 8, self.width(), 28), Qt.AlignmentFlag.AlignCenter, self.title)

        all_points = [point for _label, _color, points, _desc in self.series for point in points]
        if not all_points:
            painter.setFont(QFont("Segoe UI", 10))
            painter.setPen(QColor("#CBD5E1"))
            painter.drawText(plot, Qt.AlignmentFlag.AlignCenter, "Sem dados ainda")
            return

        min_epoch = min(point[0] for point in all_points)
        max_epoch = max(point[0] for point in all_points)
        if min_epoch == max_epoch:
            max_epoch += 1

        if self.y_range is None:
            values = [point[1] for point in all_points]
            min_y = min(values)
            max_y = max(values)
            padding = (max_y - min_y) * 0.08 or 1.0
            min_y -= padding
            max_y += padding
        else:
            min_y, max_y = self.y_range

        if min_y == max_y:
            max_y += 1

        def map_point(epoch: int, value: float) -> QPointF:
            x = plot.left() + ((epoch - min_epoch) / (max_epoch - min_epoch)) * plot.width()
            y = plot.bottom() - ((value - min_y) / (max_y - min_y)) * plot.height()
            return QPointF(x, y)

        self.draw_grid(painter, plot, min_epoch, max_epoch, min_y, max_y)

        for label, color, points, _description in self.series:
            if not points:
                continue
            polygon = QPolygonF([map_point(epoch, value) for epoch, value in points])
            painter.setPen(QPen(color, 1.4))
            painter.drawPolyline(polygon)

        self.draw_legend(painter, plot)
        painter.setPen(QColor("#E5E7EB"))
        painter.setFont(QFont("Segoe UI", 9))
        painter.drawText(
            QRectF(plot.left(), self.height() - 42, plot.width(), 24),
            Qt.AlignmentFlag.AlignCenter,
            self.x_label,
        )
        painter.save()
        painter.translate(20, plot.center().y())
        painter.rotate(-90)
        painter.drawText(QRectF(-plot.height() / 2, 0, plot.height(), 24), Qt.AlignmentFlag.AlignCenter, self.y_label)
        painter.restore()

    def draw_grid(
        self,
        painter: QPainter,
        plot: QRectF,
        min_epoch: int,
        max_epoch: int,
        min_y: float,
        max_y: float,
    ) -> None:
        painter.setFont(QFont("Segoe UI", 8))
        grid_pen = QPen(QColor("#253044"), 1)
        axis_pen = QPen(QColor("#94A3B8"), 1.0)

        for index in range(6):
            ratio = index / 5
            x = plot.left() + ratio * plot.width()
            epoch = round(min_epoch + ratio * (max_epoch - min_epoch))
            painter.setPen(grid_pen)
            painter.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))
            painter.setPen(QColor("#CBD5E1"))
            painter.drawText(QRectF(x - 30, plot.bottom() + 6, 60, 18), Qt.AlignmentFlag.AlignCenter, str(epoch))

            y = plot.bottom() - ratio * plot.height()
            value = min_y + ratio * (max_y - min_y)
            painter.setPen(grid_pen)
            painter.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
            painter.setPen(QColor("#CBD5E1"))
            painter.drawText(QRectF(4, y - 9, 62, 18), Qt.AlignmentFlag.AlignRight, f"{value:.2g}")

        painter.setPen(axis_pen)
        painter.drawRect(plot)

    def draw_legend(self, painter: QPainter, plot: QRectF) -> None:
        x = plot.right() - 220
        y = plot.top() + 8
        painter.setFont(QFont("Segoe UI", 9))
        for label, color, _points, description in self.series:
            painter.setPen(QPen(color, 2))
            painter.drawLine(QPointF(x, y + 8), QPointF(x + 26, y + 8))
            painter.setPen(QColor("#F9FAFB"))
            painter.drawText(QRectF(x + 34, y, 195, 18), Qt.AlignmentFlag.AlignLeft, label)
            painter.setPen(QColor("#94A3B8"))
            painter.drawText(QRectF(x + 34, y + 16, 195, 18), Qt.AlignmentFlag.AlignLeft, description)
            y += 34


class TrainingSummaryWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("MahjongMaster - Resumo dos Treinos")
        self.resize(1180, 720)
        self.disabled_plot_runs: set[str] = set()

        self.run_list = QListWidget()
        self.run_list.setMinimumWidth(340)
        self.run_list.currentRowChanged.connect(self.load_selected_run)

        self.summary_label = QLabel("Selecione um treino.")
        self.summary_label.setWordWrap(True)

        self.speed_table = QTableWidget(0, 7)
        self.speed_table.setHorizontalHeaderLabels(
            ["Treino", "Epocas", "Epocas/s", "s/epoca", "best mAP50", "best epoch", "last mAP50"]
        )

        self.metrics_table = QTableWidget(0, 5)
        self.metrics_table.setHorizontalHeaderLabels(["Classe", "P", "Recall", "mAP50", "mAP50-95"])

        self.plot = TrainingPlot()
        self.comparison_plots: dict[str, TrainingPlot] = {}
        self.image_list = QListWidget()
        self.image_list.currentRowChanged.connect(self.load_selected_run_image)
        self.image_preview = QLabel("Selecione uma imagem do run.")
        self.current_run_image_path: Path | None = None
        self.image_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_preview.setMinimumSize(640, 420)
        self.image_preview.setStyleSheet("QLabel { background: #0B1220; color: #E5E7EB; border: 1px solid #374151; }")
        self.image_explanation = QLabel("Selecione uma imagem para ver a legenda.")
        self.image_explanation.setWordWrap(True)

        left = QVBoxLayout()
        left.addWidget(QLabel("Treinos salvos"))
        left.addWidget(self.run_list, stretch=1)
        self.toggle_plot_button = QPushButton("Desabilitar plotagem")
        self.toggle_plot_button.clicked.connect(self.toggle_selected_run_plotting)
        self.delete_run_button = QPushButton("Deletar treino")
        self.delete_run_button.clicked.connect(self.delete_selected_run)
        left.addWidget(self.toggle_plot_button)
        left.addWidget(self.delete_run_button)

        tabs = QTabWidget()
        tabs.addTab(self.build_comparison_tab(), "Comparativo")
        tabs.addTab(self.speed_table, "Velocidade")
        tabs.addTab(self.build_detail_tab(), "Treino selecionado")
        tabs.addTab(self.metrics_table, "Classes")
        tabs.addTab(self.build_images_tab(), "Imagens do run")

        root_layout = QHBoxLayout()
        left_panel = QWidget()
        left_panel.setLayout(left)
        root_layout.addWidget(left_panel)
        root_layout.addWidget(tabs, stretch=1)

        root = QWidget()
        root.setLayout(root_layout)
        self.setCentralWidget(root)
        self.refresh_runs()

    def build_comparison_tab(self) -> QWidget:
        content = QWidget()
        layout = QVBoxLayout()
        for column in SUMMARY_COLUMNS:
            plot = TrainingPlot()
            plot.setMinimumHeight(260)
            self.comparison_plots[column] = plot
            layout.addWidget(plot)
        content.setLayout(layout)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(content)
        return scroll

    def build_detail_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout()
        layout.addWidget(self.summary_label)
        layout.addWidget(self.plot, stretch=1)
        tab.setLayout(layout)
        return tab

    def build_images_tab(self) -> QWidget:
        tab = QWidget()
        layout = QHBoxLayout()
        self.image_list.setMaximumWidth(300)
        preview_layout = QVBoxLayout()
        preview_layout.addWidget(self.image_preview, stretch=1)
        preview_layout.addWidget(self.image_explanation)
        preview_panel = QWidget()
        preview_panel.setLayout(preview_layout)
        layout.addWidget(self.image_list)
        layout.addWidget(preview_panel, stretch=1)
        tab.setLayout(layout)
        return tab

    def refresh_runs(self) -> None:
        self.run_list.clear()
        for run_dir in self.run_dirs():
            item = QListWidgetItem(run_dir.name)
            item.setData(Qt.ItemDataRole.UserRole, str(run_dir))
            self.apply_run_item_style(item, run_dir)
            self.run_list.addItem(item)
        self.refresh_comparison()
        self.refresh_speed_table()
        if self.run_list.count():
            self.run_list.setCurrentRow(0)

    def apply_run_item_style(self, item: QListWidgetItem, run_dir: Path) -> None:
        if str(run_dir) in self.disabled_plot_runs:
            item.setText(f"{run_dir.name}  [plot off]")
            item.setForeground(QBrush(QColor("#94A3B8")))
        else:
            item.setText(run_dir.name)
            item.setForeground(QBrush(QColor("#E5E7EB")))

    def run_dirs(self) -> list[Path]:
        if not RUNS_DIR.exists():
            return []
        return sorted(
            [path for path in RUNS_DIR.iterdir() if path.is_dir() and (path / "results.csv").exists()],
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )

    def load_selected_run(self, row: int) -> None:
        if row < 0:
            return
        run_dir = Path(self.run_list.item(row).data(Qt.ItemDataRole.UserRole))
        self.update_selected_run_buttons(run_dir)
        rows = read_results_rows(run_dir)
        if not rows:
            self.summary_label.setText(f"{run_dir.name}: sem results.csv legivel.")
            return

        best_row = max(rows, key=lambda item: float_value(item.get("metrics/mAP50(B)", "")))
        last_row = rows[-1]
        best_epoch = int(float(best_row["epoch"]))
        self.summary_label.setText(
            f"Treino: {run_dir.name}\n"
            f"Epocas registradas: {len(rows)} | melhor epoca por mAP50: {best_epoch}\n"
            f"Melhor mAP50: {float_value(best_row.get('metrics/mAP50(B)', '')):.3f} | "
            f"Precision: {float_value(best_row.get('metrics/precision(B)', '')):.3f} | "
            f"Recall: {float_value(best_row.get('metrics/recall(B)', '')):.3f}\n"
            f"Ultima epoca mAP50: {float_value(last_row.get('metrics/mAP50(B)', '')):.3f}\n"
            f"Modelo recomendado: {run_dir / 'weights' / 'best.pt'}"
        )
        self.plot.set_data(
            "Metricas de validacao - maior e melhor",
            "score",
            metric_series(rows),
            (0, 1),
        )
        self.load_class_table(run_dir)
        self.load_run_images(run_dir)

    def update_selected_run_buttons(self, run_dir: Path) -> None:
        if str(run_dir) in self.disabled_plot_runs:
            self.toggle_plot_button.setText("Habilitar plotagem")
        else:
            self.toggle_plot_button.setText("Desabilitar plotagem")

    def selected_run_dir(self) -> Path | None:
        item = self.run_list.currentItem()
        if item is None:
            return None
        return Path(item.data(Qt.ItemDataRole.UserRole))

    def toggle_selected_run_plotting(self) -> None:
        run_dir = self.selected_run_dir()
        if run_dir is None:
            return
        key = str(run_dir)
        if key in self.disabled_plot_runs:
            self.disabled_plot_runs.remove(key)
        else:
            self.disabled_plot_runs.add(key)

        self.refresh_run_list_styles()
        self.update_selected_run_buttons(run_dir)
        self.refresh_comparison()

    def refresh_run_list_styles(self) -> None:
        for index in range(self.run_list.count()):
            item = self.run_list.item(index)
            run_dir = Path(item.data(Qt.ItemDataRole.UserRole))
            self.apply_run_item_style(item, run_dir)

    def delete_selected_run(self) -> None:
        run_dir = self.selected_run_dir()
        if run_dir is None:
            return
        if not run_dir.exists() or run_dir.parent != RUNS_DIR:
            self.summary_label.setText(f"Recusado deletar caminho inesperado: {run_dir}")
            return

        shutil.rmtree(run_dir)
        self.disabled_plot_runs.discard(str(run_dir))
        self.current_run_image_path = None
        self.image_preview.clear()
        self.image_preview.setText("Treino deletado.")
        self.summary_label.setText(f"Treino deletado: {run_dir.name}")
        self.refresh_runs()

    def refresh_comparison(self) -> None:
        runs = [
            run_dir
            for run_dir in self.run_dirs()
            if str(run_dir) not in self.disabled_plot_runs
        ]
        for column, plot in self.comparison_plots.items():
            series = []
            for index, run_dir in enumerate(runs):
                rows = read_results_rows(run_dir)
                if not rows:
                    continue
                points = [
                    (int(float(row["epoch"])), float_value(row.get(column, "")))
                    for row in rows
                ]
                series.append(
                    (
                        run_dir.name,
                        RUN_COLORS[index % len(RUN_COLORS)],
                        points,
                        "",
                    )
                )
            y_range = (0, 1) if column.startswith("metrics/") else None
            title = f"{column} - todos os treinos"
            y_label = "score" if column.startswith("metrics/") else "loss"
            plot.set_data(title, y_label, series, y_range)

    def refresh_speed_table(self) -> None:
        runs = self.run_dirs()
        self.speed_table.setRowCount(len(runs))
        for row_index, run_dir in enumerate(runs):
            rows = read_results_rows(run_dir)
            summary = self.speed_values(rows)
            values = [
                run_dir.name,
                str(summary["epochs"]),
                f"{summary['epochs_per_second']:.3f}",
                f"{summary['seconds_per_epoch']:.2f}",
                f"{summary['best_map50']:.3f}",
                str(summary["best_epoch"]),
                f"{summary['last_map50']:.3f}",
            ]
            for column, value in enumerate(values):
                self.speed_table.setItem(row_index, column, QTableWidgetItem(value))

    def speed_values(self, rows: list[dict[str, str]]) -> dict[str, float | int]:
        if not rows:
            return {
                "epochs": 0,
                "epochs_per_second": 0.0,
                "seconds_per_epoch": 0.0,
                "best_map50": 0.0,
                "best_epoch": 0,
                "last_map50": 0.0,
            }
        first_epoch = int(float(rows[0]["epoch"]))
        last_epoch = int(float(rows[-1]["epoch"]))
        elapsed = max(0.001, float_value(rows[-1].get("time", "")) - float_value(rows[0].get("time", "")))
        epochs_done = max(1, last_epoch - first_epoch)
        best_row = max(rows, key=lambda item: float_value(item.get("metrics/mAP50(B)", "")))
        return {
            "epochs": len(rows),
            "epochs_per_second": epochs_done / elapsed,
            "seconds_per_epoch": elapsed / epochs_done,
            "best_map50": float_value(best_row.get("metrics/mAP50(B)", "")),
            "best_epoch": int(float(best_row["epoch"])),
            "last_map50": float_value(rows[-1].get("metrics/mAP50(B)", "")),
        }

    def load_run_images(self, run_dir: Path) -> None:
        self.image_list.clear()
        image_paths = sorted(
            [
                path
                for path in run_dir.iterdir()
                if path.suffix.lower() in {".png", ".jpg", ".jpeg"}
            ],
            key=lambda path: path.name.lower(),
        )
        for path in image_paths:
            item = QListWidgetItem(path.name)
            item.setData(Qt.ItemDataRole.UserRole, str(path))
            self.image_list.addItem(item)
        if self.image_list.count():
            self.image_list.setCurrentRow(0)
        else:
            self.image_preview.setText("Nenhuma imagem encontrada neste run.")

    def load_selected_run_image(self, row: int) -> None:
        if row < 0:
            return
        self.current_run_image_path = Path(self.image_list.item(row).data(Qt.ItemDataRole.UserRole))
        self.image_explanation.setText(self.run_image_explanation(self.current_run_image_path))
        self.refresh_run_image_preview()

    def run_image_explanation(self, path: Path) -> str:
        name = path.name
        explanations = {
            "F1_curve.png": (
                "F1-Confidence: mostra o F1 para cada limiar de confianca. "
                "F1 combina precisao e recall; o pico da linha azul indica o limiar que melhor equilibrou falso positivo e peca perdida."
            ),
            "P_curve.png": (
                "Precision-Confidence: mostra como a precisao muda ao aumentar a confianca minima. "
                "Quanto maior a confianca, menos deteccoes entram; normalmente sobem acertos relativos, mas podem sobrar poucas pecas."
            ),
            "R_curve.png": (
                "Recall-Confidence: mostra quantas pecas reais sao encontradas conforme a confianca minima muda. "
                "Ao aumentar a confianca, o recall geralmente cai porque mais deteccoes sao descartadas."
            ),
            "PR_curve.png": (
                "Precision-Recall: mostra o tradeoff entre encontrar mais pecas e evitar falsos positivos. "
                "Curvas mais proximas do canto superior direito sao melhores."
            ),
            "confusion_matrix.png": (
                "Matriz de confusao: eixo X e classe real, eixo Y e classe prevista. "
                "Diagonal significa acerto. Linha background indica pecas perdidas; coluna background indica falso positivo."
            ),
            "confusion_matrix_normalized.png": (
                "Matriz de confusao normalizada: igual a matriz comum, mas em proporcao. "
                "Ajuda a comparar classes com quantidades diferentes de exemplos."
            ),
            "results.png": (
                "Resumo padrao do YOLO com losses e metricas por epoca. "
                "Use best.pt, que corresponde a melhor validacao, nao necessariamente a ultima epoca."
            ),
            "labels.jpg": (
                "Distribuicao das anotacoes do dataset: quantidade, tamanhos e posicoes das boxes. "
                "Serve para ver vieses, classes raras e concentracao espacial."
            ),
        }
        if name in explanations:
            return explanations[name]
        if name.startswith("train_batch"):
            return (
                "Batch de treino: visualizacao das imagens usadas no treino com augmentations/mosaic/resize. "
                "Nao representa a resolucao original; e uma montagem para inspecionar as anotacoes."
            )
        if name.startswith("val_batch") and "pred" in name:
            return (
                "Predicoes na validacao: compara o comportamento do modelo em imagens que nao foram usadas para aprender. "
                "Bom para ver falsos positivos, pecas perdidas e confusoes visuais."
            )
        if name.startswith("val_batch") and "labels" in name:
            return "Labels da validacao: mostra as anotacoes reais usadas para avaliar o modelo."
        return "Imagem gerada pelo YOLO para diagnostico do treino."

    def refresh_run_image_preview(self) -> None:
        if self.current_run_image_path is None:
            return
        pixmap = QPixmap(str(self.current_run_image_path))
        if pixmap.isNull():
            self.image_preview.setText(f"Nao foi possivel abrir {self.current_run_image_path.name}")
            return
        scaled = pixmap.scaled(
            self.image_preview.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.image_preview.setPixmap(scaled)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self.refresh_run_image_preview()

    def load_class_table(self, run_dir: Path) -> None:
        csv_path = run_dir / "final_class_metrics.csv"
        if not csv_path.exists():
            self.metrics_table.setRowCount(0)
            return
        with csv_path.open("r", encoding="utf-8", newline="") as file:
            rows = list(csv.DictReader(file))
        self.metrics_table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            values = [
                row.get("class", ""),
                row.get("precision", ""),
                row.get("recall", ""),
                row.get("map50", ""),
                row.get("map50_95", ""),
            ]
            for column, value in enumerate(values):
                self.metrics_table.setItem(row_index, column, QTableWidgetItem(value))


class TrainingWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("MahjongMaster - Treino YOLO")
        self.resize(1120, 760)

        self.process: QProcess | None = None
        self.current_run_dir: Path | None = None
        self.run_name_is_manual = False

        self.run_name_input = QLineEdit()
        self.run_name_input.setPlaceholderText("Automatico: 800e_yolo11n_1600p")
        self.run_name_input.textEdited.connect(self.mark_run_name_manual)
        self.model_combo = QComboBox()
        self.model_combo.setEditable(True)
        self.populate_training_models()
        self.model_combo.currentTextChanged.connect(self.update_auto_run_name)
        self.device_combo = QComboBox()
        self.populate_devices()
        self.epochs_input = QSpinBox()
        self.epochs_input.setRange(1, 5000)
        self.epochs_input.setValue(800)
        self.epochs_input.valueChanged.connect(self.update_auto_run_name)
        self.imgsz_input = QSpinBox()
        self.imgsz_input.setRange(320, 2048)
        self.imgsz_input.setSingleStep(32)
        self.imgsz_input.setValue(1600)
        self.imgsz_input.valueChanged.connect(self.update_auto_run_name)
        self.batch_input = QSpinBox()
        self.batch_input.setRange(1, 128)
        self.batch_input.setValue(5)
        self.batch_input.valueChanged.connect(self.update_auto_run_name)
        self.patience_input = QSpinBox()
        self.patience_input.setRange(0, 2000)
        self.patience_input.setValue(0)

        self.start_button = QPushButton("Iniciar treino")
        self.start_button.clicked.connect(self.start_training)
        self.stop_button = QPushButton("Parar")
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self.stop_training)
        self.summary_button = QPushButton("Resumo dos treinos")
        self.summary_button.clicked.connect(self.open_summary)
        self.colab_zip_button = QPushButton("Criar dataset Colab")
        self.colab_zip_button.clicked.connect(self.create_colab_dataset_zip)
        self.summary_window: TrainingSummaryWindow | None = None

        self.status_label = QLabel(self.dataset_summary())
        self.status_label.setWordWrap(True)
        self.speed_label = QLabel("Velocidade: aguardando treino")
        self.speed_label.setWordWrap(True)
        self.help_button = QPushButton("?")
        self.help_button.setFixedWidth(32)
        self.help_button.setToolTip("Ajuda das opcoes e metricas do treino")
        self.help_button.clicked.connect(self.show_training_help)
        self.log_output = QPlainTextEdit()
        self.log_output.setReadOnly(True)

        self.train_plot = TrainingPlot()
        self.val_plot = TrainingPlot()
        self.metrics_plot = TrainingPlot()

        form = QFormLayout()
        form.addRow("Nome do treino", self.run_name_input)
        form.addRow("Modelo base", self.model_combo)
        form.addRow("Epocas", self.epochs_input)
        form.addRow("Imagem", self.imgsz_input)
        form.addRow("Batch", self.batch_input)
        form.addRow("Patience", self.patience_input)
        form.addRow("Device", self.device_combo)

        controls = QHBoxLayout()
        controls.addWidget(self.start_button)
        controls.addWidget(self.stop_button)
        controls.addWidget(self.summary_button)
        controls.addWidget(self.colab_zip_button)
        controls.addWidget(self.help_button)

        left = QVBoxLayout()
        left.addLayout(form)
        left.addLayout(controls)
        left.addWidget(self.status_label)
        left.addWidget(self.speed_label)
        left.addWidget(QLabel("Log"))
        left.addWidget(self.log_output, stretch=1)

        root_layout = QHBoxLayout()
        left_panel = QWidget()
        left_panel.setMaximumWidth(360)
        left_panel.setLayout(left)
        plots = QVBoxLayout()
        plots.addWidget(self.train_plot)
        plots.addWidget(self.val_plot)
        plots.addWidget(self.metrics_plot)
        plots_panel = QWidget()
        plots_panel.setLayout(plots)
        root_layout.addWidget(left_panel)
        root_layout.addWidget(plots_panel, stretch=1)

        root = QWidget()
        root.setLayout(root_layout)
        self.setCentralWidget(root)

        self.poll_timer = QTimer(self)
        self.poll_timer.setInterval(1000)
        self.poll_timer.timeout.connect(self.refresh_plot)

        self.update_auto_run_name()
        self.refresh_plot()

    def populate_devices(self) -> None:
        self.device_combo.addItem("CPU", "cpu")
        try:
            import torch
        except ImportError:
            return

        if not torch.cuda.is_available():
            return

        for index in range(torch.cuda.device_count()):
            name = torch.cuda.get_device_name(index)
            self.device_combo.addItem(f"GPU {index}: {name}", str(index))
        self.device_combo.setCurrentIndex(1)

    def build_help_group(self, title: str, label: QLabel) -> QGroupBox:
        group = QGroupBox(title)
        layout = QVBoxLayout()
        layout.addWidget(label)
        group.setLayout(layout)
        return group

    def show_training_help(self) -> None:
        message = (
            f"{self.training_options_help()}\n\n"
            "Metricas:\n"
            "Losses: menor e melhor. Metricas: maior e melhor.\n"
            "best.pt e escolhido pela melhor validacao, nao pela ultima epoca.\n"
            "Ao final, o app avalia esse best.pt no split test sem treinar de novo.\n\n"
            "Modelo final:\n"
            "Use weights/best.pt para testar e integrar. weights/last.pt e apenas a ultima epoca."
        )
        QMessageBox.information(self, "Ajuda do treino", message)

    def training_options_help(self) -> str:
        return (
            "Modelo base: checkpoint inicial; YOLO e rapido; RT-DETR tende a ser mais pesado/preciso.\n"
            "Epocas: voltas completas pelo dataset. Muitas epocas podem decorar se houver poucas imagens.\n"
            "Imagem: tamanho usado no treino; maior ajuda pecas pequenas e consome mais VRAM.\n"
            "Batch: imagens por passo; maior usa mais VRAM e costuma aproveitar melhor a GPU.\n"
            "Patience: quantas epocas sem melhora antes de parar; 0 desativa EarlyStopping.\n"
            "Device: CPU ou GPU CUDA detectada."
        )

    def dataset_summary(self) -> str:
        train_images = self.count_files(PROJECT_ROOT / "dataset" / "images" / "train", {".png", ".jpg", ".jpeg"})
        train_labels = self.count_files(PROJECT_ROOT / "dataset" / "labels" / "train", {".txt"})
        val_images = self.count_files(PROJECT_ROOT / "dataset" / "images" / "val", {".png", ".jpg", ".jpeg"})
        val_labels = self.count_files(PROJECT_ROOT / "dataset" / "labels" / "val", {".txt"})
        test_images = self.count_files(PROJECT_ROOT / "dataset" / "images" / "test", {".png", ".jpg", ".jpeg"})
        test_labels = self.count_files(PROJECT_ROOT / "dataset" / "labels" / "test", {".txt"})
        return (
            f"Dataset: train {train_images} imgs/{train_labels} labels | "
            f"val {val_images} imgs/{val_labels} labels | "
            f"test {test_images} imgs/{test_labels} labels"
        )

    def create_colab_dataset_zip(self) -> None:
        selected_path, _filter = QFileDialog.getSaveFileName(
            self,
            "Salvar dataset para o Colab",
            str(COLAB_DATASET_ZIP),
            "Zip (*.zip)",
        )
        if not selected_path:
            return

        output_path = Path(selected_path)
        if output_path.suffix.lower() != ".zip":
            output_path = output_path.with_suffix(".zip")

        try:
            file_count = create_dataset_zip(output_path)
        except OSError as error:
            message = f"Falha ao criar {output_path.name}: {error}"
            self.status_label.setText(message)
            self.log_output.appendPlainText(message)
            return

        size_mb = output_path.stat().st_size / (1024 * 1024)
        message = f"Dataset Colab criado: {output_path} | {file_count} arquivos | {size_mb:.1f} MB"
        self.status_label.setText(f"{self.dataset_summary()} | {message}")
        self.log_output.appendPlainText(message)

    def count_files(self, directory: Path, extensions: set[str]) -> int:
        if not directory.exists():
            return 0
        return sum(1 for path in directory.iterdir() if path.suffix.lower() in extensions)

    def start_training(self) -> None:
        if self.process is not None:
            return

        RUNS_DIR.mkdir(parents=True, exist_ok=True)
        run_name = self.training_run_name()
        self.current_run_dir = RUNS_DIR / run_name
        self.log_output.clear()
        self.status_label.setText(f"{self.dataset_summary()} | Rodando: {run_name}")
        self.speed_label.setText("Velocidade: aguardando primeira epoca")

        args = [
            "-u",
            str(TRAIN_SCRIPT),
            "--model",
            self.selected_training_model(),
            "--data",
            str(DATA_YAML),
            "--epochs",
            str(self.epochs_input.value()),
            "--imgsz",
            str(self.imgsz_input.value()),
            "--batch",
            str(self.batch_input.value()),
            "--patience",
            str(self.patience_input.value()),
            "--device",
            str(self.device_combo.currentData() or "cpu"),
            "--name",
            run_name,
            "--project",
            str(RUNS_DIR),
            "--exist-ok",
        ]

        self.process = QProcess(self)
        self.process.setWorkingDirectory(str(PROJECT_ROOT))
        self.process.setProgram(sys.executable)
        self.process.setArguments(args)
        self.process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.process.readyReadStandardOutput.connect(self.append_training_output)
        self.process.finished.connect(self.training_finished)
        self.process.start()

        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.poll_timer.start()

    def open_summary(self) -> None:
        if self.summary_window is None:
            self.summary_window = TrainingSummaryWindow()
        else:
            self.summary_window.refresh_runs()
        self.summary_window.show()
        self.summary_window.raise_()
        self.summary_window.activateWindow()

    def training_run_name(self) -> str:
        raw_name = self.run_name_input.text().strip() or self.default_training_run_name()
        safe_base_name = "".join(
            char if char.isalnum() or char in ("-", "_") else "_"
            for char in raw_name
        ).strip("_")
        if not safe_base_name:
            safe_base_name = self.default_training_run_name()

        candidate = safe_base_name
        version = 2
        while (RUNS_DIR / candidate).exists():
            candidate = f"{safe_base_name}_V{version}"
            version += 1
        return candidate

    def default_training_run_name(self) -> str:
        model_name = Path(self.selected_training_model()).stem
        return (
            f"{self.epochs_input.value()}e_{model_name}_"
            f"{self.imgsz_input.value()}p_{self.batch_input.value()}b"
        )

    def mark_run_name_manual(self) -> None:
        self.run_name_is_manual = bool(self.run_name_input.text().strip())

    def update_auto_run_name(self) -> None:
        if self.run_name_is_manual:
            return
        self.run_name_input.blockSignals(True)
        self.run_name_input.setText(self.default_training_run_name())
        self.run_name_input.blockSignals(False)

    def populate_training_models(self) -> None:
        defaults = {
            "yolo11n.pt": "Nano - mais leve/rapido, menor precisao",
            "yolo11s.pt": "Small - leve, melhor equilibrio inicial",
            "yolo11m.pt": "Medium - mais preciso, mais VRAM/tempo",
            "yolo11l.pt": "Large - pesado, melhor para dataset maior",
            "yolo11x.pt": "XLarge - mais pesado, maior custo de treino",
            "rtdetr-l.pt": "RT-DETR Large - transformer, preciso e mais lento",
            "rtdetr-x.pt": "RT-DETR XLarge - mais pesado/preciso, exige mais VRAM",
        }
        local_models = sorted(
            {
                path.name
                for path in PROJECT_ROOT.glob("*.pt")
                if path.is_file()
            }
        )
        for model_name, description in defaults.items():
            self.model_combo.addItem(f"{model_name}  ({description})", model_name)
        for model_name in [name for name in local_models if name not in defaults]:
            self.model_combo.addItem(f"{model_name}  (arquivo local)", model_name)
        self.model_combo.setCurrentIndex(0)

    def selected_training_model(self) -> str:
        return str(self.model_combo.currentData() or self.model_combo.currentText().strip() or "yolo11n.pt")

    def stop_training(self) -> None:
        if self.process is None:
            return

        self.process.terminate()
        if not self.process.waitForFinished(3000):
            self.process.kill()

    def append_training_output(self) -> None:
        if self.process is None:
            return

        output = bytes(self.process.readAllStandardOutput()).decode(
            "utf-8",
            errors="replace",
        )
        self.log_output.appendPlainText(output.rstrip())

    def training_finished(self, *_args) -> None:
        self.poll_timer.stop()
        self.append_training_output()
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.process = None
        self.save_final_class_metrics()
        if self.current_run_dir is not None:
            best_path = self.current_run_dir / "weights" / "best.pt"
            test_dir = self.current_run_dir / "test"
            self.status_label.setText(
                f"{self.dataset_summary()} | Treino finalizado | best.pt: {best_path} | test: {test_dir}"
            )
        else:
            self.status_label.setText(f"{self.dataset_summary()} | Treino finalizado")
        self.refresh_plot()

    def save_final_class_metrics(self) -> None:
        if self.current_run_dir is None:
            return

        rows = []
        for raw_line in self.log_output.toPlainText().splitlines():
            line = ANSI_RE.sub("", raw_line).strip()
            parts = line.split()
            if len(parts) < 6 or parts[0] not in TILE_CLASSES:
                continue
            rows.append(
                {
                    "class": parts[0],
                    "images": parts[1],
                    "instances": parts[2],
                    "precision": parts[3],
                    "recall": parts[4],
                    "map50": parts[5],
                    "map50_95": parts[6] if len(parts) > 6 else "",
                }
            )

        if not rows:
            return

        output_path = self.current_run_dir / "final_class_metrics.csv"
        with output_path.open("w", encoding="utf-8", newline="") as file:
            writer = csv.DictWriter(
                file,
                fieldnames=["class", "images", "instances", "precision", "recall", "map50", "map50_95"],
            )
            writer.writeheader()
            writer.writerows(rows)

    def refresh_plot(self) -> None:
        rows = self.read_results_rows()

        if not rows:
            self.train_plot.set_data("Loss de treino - menor e melhor", "loss", [])
            self.val_plot.set_data("Loss de validacao - menor e melhor", "loss", [])
            self.metrics_plot.set_data("Metricas de validacao - maior e melhor", "score", [])
            self.speed_label.setText("Velocidade: aguardando treino")
            return

        self.train_plot.set_data("Loss de treino - menor e melhor", "loss", loss_train_series(rows))
        self.val_plot.set_data("Loss de validacao - menor e melhor", "loss", loss_val_series(rows))
        self.metrics_plot.set_data("Metricas de validacao - maior e melhor", "score", metric_series(rows), (0, 1))
        self.speed_label.setText(self.training_speed_summary(rows))

    def training_speed_summary(self, rows: list[dict[str, str]]) -> str:
        if not rows:
            return "Velocidade: aguardando treino"

        first_epoch = int(float(rows[0]["epoch"]))
        last_epoch = int(float(rows[-1]["epoch"]))
        last_time = float_value(rows[-1].get("time", ""))

        if len(rows) < 2 or last_time <= 0:
            return f"Velocidade: epoca {last_epoch}, tempo {last_time:.1f}s"

        first_time = float_value(rows[0].get("time", ""))
        elapsed = max(0.001, last_time - first_time)
        epochs_done = max(1, last_epoch - first_epoch)
        epochs_per_second = epochs_done / elapsed
        seconds_per_epoch = elapsed / epochs_done
        return (
            f"Velocidade media: {epochs_per_second:.3f} epocas/s "
            f"({seconds_per_epoch:.2f}s/epoca) | epoca {last_epoch}"
        )

    def read_results_rows(self) -> list[dict[str, str]]:
        if self.current_run_dir is None:
            self.current_run_dir = self.latest_run_dir()

        if self.current_run_dir is None:
            return []

        return read_results_rows(self.current_run_dir)

    def latest_run_dir(self) -> Path | None:
        if not RUNS_DIR.exists():
            return None

        candidates = [
            path
            for path in RUNS_DIR.iterdir()
            if path.is_dir() and (path / "results.csv").exists()
        ]
        if not candidates:
            return None

        return max(candidates, key=lambda path: path.stat().st_mtime)
