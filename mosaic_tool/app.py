"""メインウィンドウ: ツールバー、ナビゲーション、保存、D&D 受付"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QActionGroup, QKeySequence
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QSlider,
    QSpinBox,
)

from mosaic_tool import io_utils
from mosaic_tool.canvas import MosaicCanvas, ToolMode
from mosaic_tool.detect import paths as detect_paths
from mosaic_tool.detect.convert import detections_to_regions
from mosaic_tool.detect.detect_window import DetectWindow
from mosaic_tool.detect.setup_dialog import RuntimeSetupDialog
from mosaic_tool.detect.worker_client import DetectWorker
from mosaic_tool.mosaic import apply_mosaic
from mosaic_tool.regions import Region
from mosaic_tool.settings import AppSettings
from mosaic_tool.version import APP_NAME, __version__

TITLE = f"{APP_NAME} v{__version__}"
BLOCK_STEP = 5      # モザイクサイズの刻み幅 (px)
BLOCK_MAX = 100     # モザイクサイズの上限 (px)
PEN_STEP = 5        # ペン太さの矢印ボタンの刻み幅 (px。数値入力は 1px 刻み)
PEN_MIN = 5         # ペン太さの下限 (px)
PEN_MAX = 200       # ペン太さの上限 (px)
THRESHOLD_STEP = 5  # しきい値の矢印ボタンの刻み幅 (%。数値入力は 1% 刻み)
THRESHOLD_MIN = 0   # マス単位判定のしきい値の下限 (%)
THRESHOLD_MAX = 100  # 同上限 (%)

_MODE_BY_KEY = {"rect": ToolMode.RECT, "pen": ToolMode.PEN}
_KEY_BY_MODE = {mode: key for key, mode in _MODE_BY_KEY.items()}


class MainWindow(QMainWindow):
    def __init__(self, paths: list[str] | None = None, settings: AppSettings | None = None):
        super().__init__()
        self._settings = settings or AppSettings()
        self.setWindowTitle(TITLE)
        self.resize(1200, 800)
        geometry = self._settings.geometry()
        if geometry is not None:
            self.restoreGeometry(geometry)
        self.setAcceptDrops(True)
        self.canvas = MosaicCanvas(self)
        # QGraphicsView は既定でドロップを受け取ってしまうため、
        # 画像上へのドロップもウィンドウ側で処理できるよう無効化する
        self.canvas.setAcceptDrops(False)
        self.setCentralWidget(self.canvas)
        self.canvas.regions_changed.connect(self._on_regions_changed)
        self.statusBar().showMessage("画像ファイルまたはフォルダをドロップしてください")

        self._images: list[Path] = []          # 編集対象の画像リスト
        self._index = 0
        self._folder: Path | None = None       # フォルダモード時の元フォルダ
        self._current_image = None             # 表示中の PIL 画像
        self._store: dict[Path, list[Region]] = {}  # 画像ごとの範囲(ナビ往復用)
        self._dirty = False                    # 未保存の変更があるか
        self._saved = False                    # 表示中の画像を一度でも保存したか
        # モザイクサイズ (px)。前回終了時の設定を復元する
        self._block = self._settings.block(BLOCK_STEP, BLOCK_MAX, BLOCK_STEP)
        # マス単位判定のしきい値 (%)。同上
        self._threshold = self._settings.threshold(THRESHOLD_MIN, THRESHOLD_MAX)

        # 自動検出(推論は別プロセスの venv 側で動く)
        self._worker = DetectWorker(self)
        self._worker.detected.connect(self._on_detected)
        self._worker.progress.connect(self._on_detect_progress)
        self._worker.failed.connect(self._on_detect_failed)
        self._detect_window: DetectWindow | None = None

        self._build_toolbar()
        if paths:
            self.open_paths([Path(p) for p in paths])

    # --- ツールバー ---

    def _add_shortcut(
        self, act: QAction, key: QKeySequence, *, canvas_only: bool = True
    ) -> None:
        """ショートカットを割り当て、tooltip に「名前 (キー)」を表示する

        canvas_only: 修飾キーなしのショートカットはツールバーのスピンボックスへの
        文字入力を奪うため、キャンバスにフォーカスがある間だけ効くようにする。
        """
        act.setShortcut(key)
        text = key.toString(QKeySequence.SequenceFormat.NativeText)
        act.setToolTip(f"{act.text()} ({text})")
        if canvas_only:
            act.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            self.canvas.addAction(act)

    def _build_toolbar(self) -> None:
        tb = self.addToolBar("ツール")
        tb.setMovable(False)
        # モード切替(矩形/ペン)。既存範囲の選択・変形はどちらのモードでも行える
        self._mode_group = QActionGroup(self)
        for name, mode, key in (
            ("ペン", ToolMode.PEN, Qt.Key.Key_1),
            ("矩形", ToolMode.RECT, Qt.Key.Key_2),
        ):
            act = QAction(name, self)
            act.setCheckable(True)
            act.setData(mode)
            self._add_shortcut(act, QKeySequence(key))
            self._mode_group.addAction(act)
            tb.addAction(act)
        saved_mode = _MODE_BY_KEY[self._settings.mode()]
        for act in self._mode_group.actions():
            act.setChecked(act.data() is saved_mode)
        self.canvas.set_mode(saved_mode)
        self._mode_group.triggered.connect(self._on_mode_changed)
        tb.addSeparator()
        # モザイクサイズ (5〜100px、5px 刻み)
        self._size_label = QLabel(f" モザイク: {self._block}px ")
        tb.addWidget(self._size_label)
        self._size_slider = QSlider(Qt.Orientation.Horizontal)
        self._size_slider.setRange(BLOCK_STEP, BLOCK_MAX)
        self._size_slider.setSingleStep(BLOCK_STEP)
        self._size_slider.setPageStep(BLOCK_STEP)
        self._size_slider.setTickInterval(BLOCK_STEP)
        self._size_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self._size_slider.setFixedWidth(200)
        self._size_slider.setValue(self._block)
        self._size_slider.valueChanged.connect(self._on_block_changed)
        # 復元値が既定値と同じ場合は valueChanged が飛ばないため明示的に反映する
        self.canvas.set_block_size(self._block)
        tb.addWidget(self._size_slider)
        tb.addSeparator()
        # しきい値: マスの被覆率がこの値以上ならそのマス全体をモザイクにする
        # (矢印ボタンは 5% 刻み、数値入力は 1% 刻み)
        tb.addWidget(QLabel(" しきい値 "))
        self._threshold_spin = QSpinBox()
        self._threshold_spin.setRange(THRESHOLD_MIN, THRESHOLD_MAX)
        self._threshold_spin.setSingleStep(THRESHOLD_STEP)
        self._threshold_spin.setValue(self._threshold)
        self._threshold_spin.setSuffix(" %")
        self._threshold_spin.valueChanged.connect(self._on_threshold_changed)
        # 同上の理由で明示的に反映する
        self.canvas.set_threshold(self._threshold / 100)
        tb.addWidget(self._threshold_spin)
        tb.addSeparator()
        # ペン太さ (5〜200px。矢印ボタンは 5px 刻み、数値入力は 1px 刻み)
        tb.addWidget(QLabel(" ペン太さ "))
        self._pen_spin = QSpinBox()
        self._pen_spin.setRange(PEN_MIN, PEN_MAX)
        self._pen_spin.setSingleStep(PEN_STEP)
        self._pen_spin.setValue(self._settings.pen_width(PEN_MIN, PEN_MAX))
        self._pen_spin.setSuffix(" px")
        self._pen_spin.valueChanged.connect(self._on_pen_width_changed)
        # 同上の理由(復元値が既定値と同じならシグナルが飛ばない)で明示的に反映する
        self.canvas.set_pen_width(float(self._pen_spin.value()))
        tb.addWidget(self._pen_spin)
        tb.addSeparator()
        # ナビゲーションと保存
        self._prev_act = QAction("◀ 前へ", self)
        self._add_shortcut(self._prev_act, QKeySequence(Qt.Key.Key_Left))
        self._prev_act.triggered.connect(lambda: self._go(self._index - 1))
        tb.addAction(self._prev_act)
        self._progress_label = QLabel(" - / - ")
        tb.addWidget(self._progress_label)
        self._next_act = QAction("次へ ▶", self)
        self._add_shortcut(self._next_act, QKeySequence(Qt.Key.Key_Right))
        self._next_act.triggered.connect(lambda: self._go(self._index + 1))
        tb.addAction(self._next_act)
        tb.addSeparator()
        save_act = QAction("保存", self)
        self._add_shortcut(
            save_act, QKeySequence(QKeySequence.StandardKey.Save), canvas_only=False
        )
        save_act.triggered.connect(self._save_current)
        tb.addAction(save_act)
        # 自動保存: 画像の切替・終了時に確認なしで保存する
        self._autosave_check = QCheckBox("自動保存")
        self._autosave_check.setChecked(self._settings.autosave())
        self._autosave_check.toggled.connect(self._settings.set_autosave)
        tb.addWidget(self._autosave_check)
        tb.addSeparator()
        # プレビュー: 範囲のアウトラインを隠して仕上がりを確認する
        self._preview_act = QAction("プレビュー", self)
        self._preview_act.setCheckable(True)
        # Tab はキャンバス上ではフォーカス移動より優先され、プレビュー切替として働く
        self._add_shortcut(self._preview_act, QKeySequence(Qt.Key.Key_Tab))
        self._preview_act.toggled.connect(self.canvas.set_preview_mode)
        tb.addAction(self._preview_act)
        tb.addSeparator()
        # 自動検出: 専用ウィンドウでモデルと信頼度を選んでから実行する
        self._detect_act = QAction("自動検出", self)
        self._add_shortcut(self._detect_act, QKeySequence(Qt.Key.Key_D))
        self._detect_act.triggered.connect(self._open_detect_window)
        tb.addAction(self._detect_act)

    def _on_block_changed(self, value: int) -> None:
        # 5px 刻みにスナップする
        snapped = max(BLOCK_STEP, round(value / BLOCK_STEP) * BLOCK_STEP)
        if snapped != value:
            self._size_slider.setValue(snapped)
            return
        self._block = snapped
        self._size_label.setText(f" モザイク: {snapped}px ")
        self.canvas.set_block_size(snapped)
        self._settings.set_block(snapped)

    def _on_threshold_changed(self, value: int) -> None:
        self._threshold = value
        self.canvas.set_threshold(value / 100)
        self._settings.set_threshold(value)

    def _on_pen_width_changed(self, value: int) -> None:
        self.canvas.set_pen_width(float(value))
        self._settings.set_pen_width(value)

    def _on_mode_changed(self, action: QAction) -> None:
        self.canvas.set_mode(action.data())
        self._settings.set_mode(_KEY_BY_MODE[action.data()])

    # --- D&D ---

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        paths = [Path(u.toLocalFile()) for u in event.mimeData().urls() if u.isLocalFile()]
        if not paths:
            return
        event.acceptProposedAction()
        self.open_paths(paths)

    # --- 読み込みとナビゲーション ---

    def open_paths(self, paths: list[Path]) -> None:
        """ドロップ/引数で渡されたパスを開く。

        フォルダは 1 つだけを対象として開き直し、画像ファイルは編集リストへ追加する。
        """
        folder = next((p for p in paths if p.is_dir()), None)
        if folder is not None:
            self._open_folder(folder)
            return
        files = [p for p in paths if p.is_file() and io_utils.is_image_file(p)]
        if not files:
            QMessageBox.warning(self, "エラー", "対応する画像ファイルが見つかりません")
            return
        self._add_files(files)

    def _open_folder(self, folder: Path) -> None:
        """フォルダ内の画像を編集リストとして開き直す"""
        images = io_utils.list_images(folder)
        if not images:
            QMessageBox.warning(
                self, "エラー", f"フォルダに対応画像がありません: {folder}"
            )
            return
        if not self._confirm_discard():
            return
        self._folder = folder
        self._images = images
        self._index = 0
        self._store = {}
        self._load_current()

    def _add_files(self, files: list[Path]) -> None:
        """画像ファイルを編集リストの末尾へ追加し、その先頭へ表示を切り替える"""
        # フォルダモード中は保存先が変わるため、追加ではなく開き直す
        if self._folder is not None:
            if not self._confirm_discard():
                return
            self._folder = None
            self._images = []
            self._index = 0
            self._store = {}
        was_empty = not self._images
        added = [p for p in files if p not in self._images]
        # 表示を切り替える先。既読み込みのみのドロップならその画像へ戻る
        target = added[0] if added else files[0]
        if not was_empty:
            if not added and target == self._images[self._index]:
                # 表示中の画像をそのまま再ドロップした場合は何もしない
                self.statusBar().showMessage("すでに読み込み済みの画像です", 5000)
                return
            if not self._confirm_discard():
                return
        self._images.extend(added)
        self._switch_to(self._images.index(target))
        if added and not was_empty:
            self.statusBar().showMessage(f"{len(added)} 件の画像を追加しました", 5000)

    def _go(self, index: int) -> None:
        """前へ/次へ。範囲は保持し、未保存なら確認する

        自動保存時に出力先を元と同じ枚数に揃えるため、ここだけは無編集でも保存する
        (ファイル/フォルダの開き直しや終了時は勝手に書き出さない)。
        """
        if not self._images or not (0 <= index < len(self._images)):
            return
        if not self._confirm_discard(save_unedited=True):
            return
        self._switch_to(index)

    def _switch_to(self, index: int) -> None:
        """表示中の範囲を保持して指定位置の画像へ切り替える(未保存確認は呼び出し側)"""
        if self._images:
            self._store[self._images[self._index]] = self.canvas.get_regions()
        self._index = index
        self._load_current()

    def _load_current(self) -> None:
        # 画像が切り替わったらプレビューは解除する (キャンバスへは toggled 経由で伝わる)
        self._preview_act.setChecked(False)
        while self._images:
            src = self._images[self._index]
            try:
                self._current_image = io_utils.load_image(src)
                break
            except Exception as e:
                # 読めない画像はリストから除外して次を試す
                QMessageBox.warning(
                    self, "読み込みエラー", f"画像を読み込めません(スキップ): {src}\n{e}"
                )
                self._images.pop(self._index)
                if self._index >= len(self._images):
                    self._index = max(0, len(self._images) - 1)
        if not self._images:
            self._current_image = None
            self.canvas.clear_image()
            self.setWindowTitle(TITLE)
            self._dirty = False
            self._saved = False
            self._update_nav()
            self._notify_image_available()
            return
        src = self._images[self._index]
        self.canvas.set_image(self._current_image, self._store.get(src, []))
        # ショートカットはキャンバスにフォーカスがある間だけ効くため、読み込み直後に移す
        self.canvas.setFocus()
        self._dirty = False
        self._saved = False
        self.setWindowTitle(f"{TITLE} - {src.name}")
        self._update_nav()
        self._notify_image_available()

    def _notify_image_available(self) -> None:
        """自動検出ウィンドウへ画像の有無を伝える(実行ボタンの可否に効く)"""
        if self._detect_window is not None:
            self._detect_window.set_image_available(bool(self._images))

    def _update_nav(self) -> None:
        total = len(self._images)
        current = self._index + 1 if total else 0
        self._progress_label.setText(f" {current} / {total} ")
        multi = total > 1
        self._prev_act.setEnabled(multi and self._index > 0)
        self._next_act.setEnabled(multi and self._index < total - 1)

    # --- 保存 ---

    def _write_current(self) -> bool:
        """表示中の画像にモザイクを適用して保存する。成功したら True"""
        if self._current_image is None or not self._images:
            return False
        src = self._images[self._index]
        if self._folder is not None:
            dest = io_utils.mc_folder_path(self._folder) / src.name
        else:
            dest = io_utils.mc_file_path(src)
        paths = self.canvas.image_paths()
        try:
            out = apply_mosaic(
                self._current_image, paths, self._block, self._threshold / 100
            )
            io_utils.save_image(out, dest)
        except Exception as e:
            QMessageBox.critical(self, "保存エラー", f"保存に失敗しました: {dest}\n{e}")
            return False
        self._dirty = False
        self._saved = True
        self.statusBar().showMessage(f"保存しました: {dest}", 5000)
        return True

    def _save_current(self) -> None:
        if not self._write_current():
            return
        # フォルダモードでは保存後に自動で次の画像へ進む
        if self._folder is not None:
            if self._index < len(self._images) - 1:
                self._go(self._index + 1)
            else:
                QMessageBox.information(
                    self, "完了", f"最後の画像です。保存先: {io_utils.mc_folder_path(self._folder)}"
                )

    # --- 自動検出 ---

    def _open_detect_window(self) -> None:
        """自動検出ウィンドウを開く(2 回目以降は前面に出す)

        推論環境が無いうちはウィンドウで何もできないため、先にセットアップを出す。
        """
        if not detect_paths.is_runtime_ready():
            if RuntimeSetupDialog(self).exec() != QDialog.DialogCode.Accepted:
                return
        if self._detect_window is None:
            window = DetectWindow(self._settings, self)
            window.detect_requested.connect(self._start_detect)
            # モデルの顔ぶれが変わったらワーカーを畳み、次回に新しい構成で起動させる
            window.models_changed.connect(self._worker.stop)
            self._detect_window = window
        self._detect_window.set_image_available(bool(self._images))
        self._detect_window.refresh()
        self._detect_window.show()
        self._detect_window.raise_()
        self._detect_window.activateWindow()

    def _start_detect(self, models: dict) -> None:
        """表示中の画像に対して自動検出を実行する"""
        if not self._images or self._current_image is None or self._worker.is_busy():
            return
        if self._detect_window is not None:
            self._detect_window.set_running(True)
        self.statusBar().showMessage("検出中...")
        self._worker.request(
            str(self._images[self._index]),
            models,
            "" if self._settings.device() == "auto" else "cpu",
        )

    def _on_detect_progress(self, done: int, total: int, _model: str) -> None:
        if self._detect_window is not None:
            self._detect_window.set_progress(done, total)

    def _finish_detect(self) -> None:
        if self._detect_window is not None:
            self._detect_window.set_running(False)

    def _on_detected(self, detections: list) -> None:
        """検出結果を範囲として追加する(既存の範囲は残す)"""
        self._finish_detect()
        if self._current_image is None:
            return
        regions = detections_to_regions(detections, self._current_image.size)
        if not regions:
            self.statusBar().showMessage("検出されませんでした", 5000)
            return
        self.canvas.add_regions(regions)
        self.statusBar().showMessage(f"{len(regions)} 件の範囲を追加しました", 5000)

    def _on_detect_failed(self, message: str) -> None:
        self._finish_detect()
        self.statusBar().clearMessage()
        QMessageBox.critical(self, "検出エラー", message)

    # --- 未保存確認 ---

    def _on_regions_changed(self) -> None:
        self._dirty = True

    def _confirm_discard(self, save_unedited: bool = False) -> bool:
        """未保存の変更があれば破棄してよいか確認する

        自動保存が有効なときは確認せず保存して続行する。
        save_unedited が True なら、無編集のまま離れる場合も保存する。
        """
        if self._autosave_check.isChecked():
            if self._dirty or (save_unedited and not self._saved):
                return self._write_current()
            return True
        if not self._dirty:
            return True
        ret = QMessageBox.question(
            self,
            "確認",
            "保存していない変更があります。破棄して続行しますか?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        return ret == QMessageBox.StandardButton.Yes

    def closeEvent(self, event):
        if self._confirm_discard():
            if self._detect_window is not None:
                self._detect_window.close()
            self._worker.stop()
            self._settings.set_geometry(self.saveGeometry())
            event.accept()
        else:
            event.ignore()
