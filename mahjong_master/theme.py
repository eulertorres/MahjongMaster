from __future__ import annotations

from PyQt6.QtGui import QPalette, QColor
from PyQt6.QtWidgets import QApplication


def apply_dark_theme(app: QApplication) -> None:
    app.setStyle("Fusion")

    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor("#111827"))
    palette.setColor(QPalette.ColorRole.WindowText, QColor("#E5E7EB"))
    palette.setColor(QPalette.ColorRole.Base, QColor("#0B1220"))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor("#162033"))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor("#111827"))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor("#F9FAFB"))
    palette.setColor(QPalette.ColorRole.Text, QColor("#E5E7EB"))
    palette.setColor(QPalette.ColorRole.Button, QColor("#1F2937"))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor("#F9FAFB"))
    palette.setColor(QPalette.ColorRole.BrightText, QColor("#FFFFFF"))
    palette.setColor(QPalette.ColorRole.Highlight, QColor("#2563EB"))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#FFFFFF"))
    app.setPalette(palette)

    app.setStyleSheet(
        """
        QWidget {
            background: #111827;
            color: #E5E7EB;
            selection-background-color: #2563EB;
            selection-color: #FFFFFF;
        }
        QMainWindow, QDialog, QStatusBar {
            background: #111827;
            color: #E5E7EB;
        }
        QLabel {
            background: transparent;
            color: #E5E7EB;
        }
        QPushButton {
            background: #1F2937;
            border: 1px solid #374151;
            border-radius: 4px;
            padding: 5px 10px;
            color: #F9FAFB;
        }
        QPushButton:hover {
            background: #263449;
            border-color: #4B5563;
        }
        QPushButton:pressed {
            background: #111827;
        }
        QPushButton:disabled {
            background: #172033;
            color: #6B7280;
            border-color: #263244;
        }
        QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox, QComboBox,
        QKeySequenceEdit {
            background: #0B1220;
            border: 1px solid #374151;
            border-radius: 4px;
            padding: 4px;
            color: #E5E7EB;
        }
        QComboBox::drop-down, QSpinBox::up-button, QSpinBox::down-button,
        QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {
            background: #1F2937;
            border-left: 1px solid #374151;
            width: 18px;
        }
        QComboBox QAbstractItemView {
            background: #0B1220;
            border: 1px solid #374151;
            color: #E5E7EB;
            selection-background-color: #2563EB;
        }
        QCheckBox, QRadioButton {
            background: transparent;
            color: #E5E7EB;
            spacing: 6px;
        }
        QCheckBox::indicator, QRadioButton::indicator {
            width: 14px;
            height: 14px;
            border: 1px solid #6B7280;
            background: #0B1220;
        }
        QCheckBox::indicator:checked, QRadioButton::indicator:checked {
            background: #2563EB;
            border-color: #60A5FA;
        }
        QListWidget, QTableWidget, QTableView, QTreeView {
            background: #0B1220;
            alternate-background-color: #111827;
            border: 1px solid #374151;
            color: #E5E7EB;
            gridline-color: #374151;
        }
        QListWidget::item, QTableWidget::item {
            padding: 3px;
        }
        QListWidget::item:selected, QTableWidget::item:selected {
            background: #2563EB;
            color: #FFFFFF;
        }
        QHeaderView::section {
            background: #1F2937;
            color: #F9FAFB;
            border: 1px solid #374151;
            padding: 4px;
        }
        QTabWidget::pane {
            border: 1px solid #374151;
            background: #111827;
        }
        QTabBar::tab {
            background: #1F2937;
            color: #D1D5DB;
            padding: 7px 10px;
            border: 1px solid #374151;
            border-bottom: none;
        }
        QTabBar::tab:selected {
            background: #111827;
            color: #FFFFFF;
        }
        QGroupBox {
            border: 1px solid #374151;
            border-radius: 4px;
            margin-top: 10px;
            padding: 8px;
        }
        QGroupBox::title {
            subcontrol-origin: margin;
            left: 8px;
            padding: 0 4px;
            color: #F9FAFB;
        }
        QScrollArea {
            background: #111827;
            border: none;
        }
        QScrollBar:vertical, QScrollBar:horizontal {
            background: #0B1220;
            width: 12px;
            height: 12px;
        }
        QScrollBar::handle:vertical, QScrollBar::handle:horizontal {
            background: #374151;
            border-radius: 5px;
        }
        QScrollBar::handle:vertical:hover, QScrollBar::handle:horizontal:hover {
            background: #4B5563;
        }
        QScrollBar::add-line, QScrollBar::sub-line {
            width: 0;
            height: 0;
        }
        """
    )
