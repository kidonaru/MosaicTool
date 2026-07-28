"""自動検出ウィンドウ: 推論環境の状態、モデルごとの設定、検出の実行と進捗"""
from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from mosaic_tool.detect import catalog, paths
from mosaic_tool.detect.setup_dialog import RuntimeSetupDialog
from mosaic_tool.settings import DEFAULT_CONFIDENCE, AppSettings

CONFIDENCE_MIN = 1    # 信頼度しきい値の下限 (%)
CONFIDENCE_MAX = 100  # 同上限 (%)
INITIAL_WIDTH = 620   # 初期サイズ。モデルが増えたぶんは一覧のスクロールで吸収する
INITIAL_HEIGHT = 300
READY_TEXT = "推論環境: 構築済み"
NOT_READY_TEXT = "推論環境: 未構築"
NO_MODEL_TEXT = (
    "モデルがありません。セットアップすると標準モデルが取得されます。\n"
    "自分で用意した .pt は models フォルダへ置いて「更新」を押してください。"
)


@dataclass
class ModelRow:
    """モデル 1 件分のウィジェット"""

    check: QCheckBox
    label: QLabel
    slider: QSlider
    value_label: QLabel


class DetectWindow(QDialog):
    """自動検出の操作をまとめたモードレスウィンドウ

    検出の実行自体は持たず、対象モデルを detect_requested で伝える。
    結果の反映(範囲の追加)はメインウィンドウ側の責務。
    """

    detect_requested = Signal(dict)   # {ファイル名: 信頼度(0.0〜1.0)}
    models_changed = Signal()         # models/ の顔ぶれが変わった

    def __init__(self, settings: AppSettings, parent=None):
        super().__init__(parent)
        self._settings = settings
        self._rows: dict[str, ModelRow] = {}
        self._filenames: list[str] = []
        self._image_available = False
        self._running = False
        self.setWindowTitle("自動検出")
        self.setModal(False)
        self.resize(INITIAL_WIDTH, INITIAL_HEIGHT)

        layout = QVBoxLayout(self)
        runtime_row = QHBoxLayout()
        self._runtime_label = QLabel()
        runtime_row.addWidget(self._runtime_label)
        runtime_row.addStretch()
        self._setup_button = QPushButton()
        self._setup_button.clicked.connect(self._on_setup_clicked)
        runtime_row.addWidget(self._setup_button)
        layout.addLayout(runtime_row)

        group = QGroupBox("モデル")
        group_layout = QVBoxLayout(group)
        header = QHBoxLayout()
        header.addStretch()
        open_button = QPushButton("フォルダを開く")
        open_button.clicked.connect(self._on_open_folder)
        header.addWidget(open_button)
        refresh_button = QPushButton("更新")
        refresh_button.clicked.connect(self.refresh)
        header.addWidget(refresh_button)
        group_layout.addLayout(header)
        self._empty_label = QLabel(NO_MODEL_TEXT)
        self._empty_label.setWordWrap(True)
        group_layout.addWidget(self._empty_label)
        # 行数が増えてもウィンドウが伸び続けないようスクロールさせる
        self._rows_widget = QWidget()
        self._grid = QGridLayout(self._rows_widget)
        self._grid.setColumnStretch(2, 1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self._rows_widget)
        group_layout.addWidget(scroll)
        layout.addWidget(group, stretch=1)

        self._bar = QProgressBar()
        self._bar.setVisible(False)
        layout.addWidget(self._bar)
        buttons = QHBoxLayout()
        buttons.addStretch()
        self._detect_button = QPushButton("検出実行")
        self._detect_button.clicked.connect(self._on_detect_clicked)
        buttons.addWidget(self._detect_button)
        close_button = QPushButton("閉じる")
        close_button.clicked.connect(self.close)
        buttons.addWidget(close_button)
        layout.addLayout(buttons)

        self.refresh()

    # --- 状態の反映 ---

    def refresh(self) -> None:
        """推論環境の状態とモデル一覧を作り直す"""
        ready = paths.is_runtime_ready()
        self._runtime_label.setText(READY_TEXT if ready else NOT_READY_TEXT)
        self._setup_button.setText("再セットアップ" if ready else "セットアップ")
        filenames = [p.name for p in paths.model_files()]
        if filenames != self._filenames:
            self._filenames = filenames
            self._rebuild_rows()
            # 一覧の顔ぶれが変わったらワーカーは古い構成のままなので伝える
            self.models_changed.emit()
        self._empty_label.setVisible(not filenames)
        self._update_detect_enabled()

    def _rebuild_rows(self) -> None:
        while self._grid.count():
            item = self._grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        # 前回の余白行が残ると行間が広がるため、伸縮指定を消してから組み直す
        for row in range(self._grid.rowCount()):
            self._grid.setRowStretch(row, 0)
        self._rows = {}
        for row, filename in enumerate(self._filenames):
            entry = catalog.find(filename)
            # 初期値はカタログの推奨値。カタログ外のモデルは全体の既定値を使う
            default = entry.confidence if entry else DEFAULT_CONFIDENCE
            check = QCheckBox(filename)
            check.setChecked(self._settings.model_enabled(filename))
            label = QLabel(entry.label if entry else "")
            slider = QSlider(Qt.Orientation.Horizontal)
            slider.setRange(CONFIDENCE_MIN, CONFIDENCE_MAX)
            slider.setValue(
                self._settings.model_confidence(
                    filename, CONFIDENCE_MIN, CONFIDENCE_MAX, default
                )
            )
            value_label = QLabel(f"{slider.value()} %")
            check.toggled.connect(
                lambda checked, name=filename: self._on_enabled_changed(name, checked)
            )
            slider.valueChanged.connect(
                lambda value, name=filename: self._on_confidence_changed(name, value)
            )
            for column, widget in enumerate((check, label, slider, value_label)):
                self._grid.addWidget(widget, row, column)
            self._rows[filename] = ModelRow(check, label, slider, value_label)
            self._apply_row_enabled(filename)
        # 末尾に余白を持たせ、行を上詰めにする(少数のときに間延びしない)
        self._grid.setRowStretch(len(self._filenames), 1)

    def _apply_row_enabled(self, filename: str) -> None:
        row = self._rows[filename]
        enabled = row.check.isChecked()
        row.slider.setEnabled(enabled)
        row.value_label.setEnabled(enabled)

    def set_image_available(self, available: bool) -> None:
        """メインウィンドウに編集中の画像があるか"""
        self._image_available = available
        self._update_detect_enabled()

    def set_running(self, running: bool) -> None:
        """検出中は実行ボタンを止め、終わったらプログレスを隠す"""
        self._running = running
        if not running:
            self._bar.setVisible(False)
        self._update_detect_enabled()

    def set_progress(self, done: int, total: int) -> None:
        self._bar.setVisible(True)
        self._bar.setRange(0, max(total, 1))
        self._bar.setValue(done)

    def enabled_models(self) -> dict:
        """有効なモデルとその信頼度(0.0〜1.0)"""
        return {
            name: row.slider.value() / 100
            for name, row in self._rows.items()
            if row.check.isChecked()
        }

    def _update_detect_enabled(self) -> None:
        self._detect_button.setEnabled(
            paths.is_runtime_ready()
            and self._image_available
            and not self._running
            and bool(self.enabled_models())
        )

    # --- 操作 ---

    def _on_enabled_changed(self, filename: str, checked: bool) -> None:
        self._settings.set_model_enabled(filename, checked)
        self._apply_row_enabled(filename)
        self._update_detect_enabled()

    def _on_confidence_changed(self, filename: str, value: int) -> None:
        self._settings.set_model_confidence(filename, value)
        self._rows[filename].value_label.setText(f"{value} %")

    def _on_setup_clicked(self) -> None:
        RuntimeSetupDialog(self).exec()
        self.refresh()

    def _on_open_folder(self) -> None:
        directory = paths.models_dir()
        directory.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(directory)))

    def _on_detect_clicked(self) -> None:
        models = self.enabled_models()
        if not models:
            return
        self.set_progress(0, len(models))
        self.detect_requested.emit(models)
