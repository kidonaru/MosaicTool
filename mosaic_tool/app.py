"""メインウィンドウ: ツールバー、ナビゲーション、保存、D&D 受付"""
from __future__ import annotations

import io
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image
from PySide6.QtCore import QProcess, Qt
from PySide6.QtGui import QAction, QActionGroup, QKeySequence
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressDialog,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from mosaic_tool import io_utils
from mosaic_tool.canvas import MosaicCanvas, ToolMode
from mosaic_tool.detect import paths as detect_paths
from mosaic_tool.detect.convert import detections_to_regions
from mosaic_tool.detect.detect_window import DetectWindow
from mosaic_tool.detect.runtime import resolve_device
from mosaic_tool.detect.setup_dialog import RuntimeSetupDialog
from mosaic_tool.detect.worker_client import DetectWorker
from mosaic_tool.flow_toolbar import FlowToolBar
from mosaic_tool.mosaic import apply_mosaic
from mosaic_tool.regions import Region, drop_duplicate_regions
from mosaic_tool.settings import AppSettings
from mosaic_tool.version import APP_NAME, __version__
from mosaic_tool.video import ffmpeg as video_ffmpeg
from mosaic_tool.video.exporter import VideoExporter
from mosaic_tool.video.merge import Detection, merge_detections, parse_detection
from mosaic_tool.video.session import VideoSession
from mosaic_tool.video.setup_dialog import VideoSetupDialog
from mosaic_tool.video.timeline import TimelineBar

TITLE = f"{APP_NAME} v{__version__}"
BLOCK_STEP = 5      # モザイクサイズの刻み幅 (px)
BLOCK_MAX = 100     # モザイクサイズの上限 (px)
BLOCK_SLIDER_WIDTH = 100  # モザイクサイズのスライダー幅 (px)
BLOCK_LABEL_WIDTH = 50    # モザイクサイズ表示の幅 (px。"100px" が収まる幅)
PEN_STEP = 5        # ペン太さの矢印ボタンの刻み幅 (px。数値入力は 1px 刻み)
PEN_MIN = 5         # ペン太さの下限 (px)
PEN_MAX = 200       # ペン太さの上限 (px)
THRESHOLD_STEP = 5  # しきい値の矢印ボタンの刻み幅 (%。数値入力は 1% 刻み)
THRESHOLD_MIN = 0   # マス単位判定のしきい値の下限 (%)
THRESHOLD_MAX = 100  # 同上限 (%)

_MODE_BY_KEY = {"rect": ToolMode.RECT, "pen": ToolMode.PEN}
_KEY_BY_MODE = {mode: key for key, mode in _MODE_BY_KEY.items()}


@dataclass
class _VideoDetectState:
    """動画への自動検出 1 回分の実行状態(フレーム展開〜ワーカー巡回で共有する)"""

    models: dict
    step: int
    dir: Path
    proc: QProcess
    files: list[Path] = field(default_factory=list)
    idx: int = 0
    dets: list[Detection] = field(default_factory=list)


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
        # ツールバーを折り返し可能にするため、QMainWindow のツールバー領域ではなく
        # キャンバスと縦に並べた自前のコンテナへ載せる
        self._toolbar = FlowToolBar(self)
        container = QWidget(self)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._toolbar)
        layout.addWidget(self.canvas)
        # タイムライン(動画モードのときだけ表示する)
        self._timeline = TimelineBar(self)
        self._timeline.hide()
        self._timeline.frame_changed.connect(self._on_frame_changed)
        self._timeline.interval_edited.connect(self._on_interval_edited)
        self._timeline.interval_clicked.connect(self._on_interval_clicked)
        layout.addWidget(self._timeline)
        self.setCentralWidget(container)
        self.canvas.regions_changed.connect(self._on_regions_changed)
        self.canvas.selection_changed.connect(self._update_selection_interval)
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
        self._worker.classes_received.connect(self._on_classes_received)
        self._detect_window: DetectWindow | None = None
        # 全ファイル実行中のモデル構成(None なら通常の 1 枚ずつの検出)
        self._batch_models: dict | None = None

        # 動画モードの状態(None なら画像モード)
        self._video: VideoSession | None = None
        self._video_displayed_ids: set[int] = set()  # 直近の表示に渡した Region の id
        self._exporter: VideoExporter | None = None
        self._export_dialog: QProgressDialog | None = None
        # 動画への自動検出の実行状態(None なら未実行)
        self._video_detect: _VideoDetectState | None = None

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
        tb = self._toolbar
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
        tb.add_separator()
        # モザイクサイズ (5〜100px、5px 刻み)
        tb.add_widget(QLabel(" モザイク "))
        self._size_slider = QSlider(Qt.Orientation.Horizontal)
        self._size_slider.setRange(BLOCK_STEP, BLOCK_MAX)
        self._size_slider.setSingleStep(BLOCK_STEP)
        self._size_slider.setPageStep(BLOCK_STEP)
        self._size_slider.setTickInterval(BLOCK_STEP)
        self._size_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self._size_slider.setFixedWidth(BLOCK_SLIDER_WIDTH)
        self._size_slider.setValue(self._block)
        self._size_slider.valueChanged.connect(self._on_block_changed)
        # 復元値が既定値と同じ場合は valueChanged が飛ばないため明示的に反映する
        self.canvas.set_block_size(self._block)
        tb.add_widget(self._size_slider)
        # 現在値はスライダーの右に表示する。桁数でツールバーが動かないよう幅を固定する
        self._size_label = QLabel(f" {self._block}px ")
        self._size_label.setFixedWidth(BLOCK_LABEL_WIDTH)
        tb.add_widget(self._size_label)
        tb.add_separator()
        # しきい値: マスの被覆率がこの値以上ならそのマス全体をモザイクにする
        # (矢印ボタンは 5% 刻み、数値入力は 1% 刻み)
        tb.add_widget(QLabel(" しきい値 "))
        self._threshold_spin = QSpinBox()
        self._threshold_spin.setRange(THRESHOLD_MIN, THRESHOLD_MAX)
        self._threshold_spin.setSingleStep(THRESHOLD_STEP)
        self._threshold_spin.setValue(self._threshold)
        self._threshold_spin.setSuffix(" %")
        self._threshold_spin.valueChanged.connect(self._on_threshold_changed)
        # 同上の理由で明示的に反映する
        self.canvas.set_threshold(self._threshold / 100)
        tb.add_widget(self._threshold_spin)
        tb.add_separator()
        # ペン太さ (5〜200px。矢印ボタンは 5px 刻み、数値入力は 1px 刻み)
        tb.add_widget(QLabel(" ペン太さ "))
        self._pen_spin = QSpinBox()
        self._pen_spin.setRange(PEN_MIN, PEN_MAX)
        self._pen_spin.setSingleStep(PEN_STEP)
        self._pen_spin.setValue(self._settings.pen_width(PEN_MIN, PEN_MAX))
        self._pen_spin.setSuffix(" px")
        self._pen_spin.valueChanged.connect(self._on_pen_width_changed)
        # 同上の理由(復元値が既定値と同じならシグナルが飛ばない)で明示的に反映する
        self.canvas.set_pen_width(float(self._pen_spin.value()))
        tb.add_widget(self._pen_spin)
        tb.add_separator()
        # ナビゲーションと保存
        self._prev_act = QAction("◀ 前へ", self)
        self._add_shortcut(self._prev_act, QKeySequence(Qt.Key.Key_Left))
        self._prev_act.triggered.connect(lambda: self._go(self._index - 1))
        tb.addAction(self._prev_act)
        self._progress_label = QLabel(" - / - ")
        tb.add_widget(self._progress_label)
        self._next_act = QAction("次へ ▶", self)
        self._add_shortcut(self._next_act, QKeySequence(Qt.Key.Key_Right))
        self._next_act.triggered.connect(lambda: self._go(self._index + 1))
        tb.addAction(self._next_act)
        tb.add_separator()
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
        tb.add_widget(self._autosave_check)
        # メタ削除: Exif や ICC プロファイル等を引き継がずに保存する
        self._strip_meta_check = QCheckBox("メタ削除")
        self._strip_meta_check.setToolTip("Exif / ICC プロファイル等のメタ情報を削除して保存する")
        self._strip_meta_check.setChecked(self._settings.strip_meta())
        self._strip_meta_check.toggled.connect(self._settings.set_strip_meta)
        tb.add_widget(self._strip_meta_check)
        tb.add_separator()
        # プレビュー: 範囲のアウトラインを隠して仕上がりを確認する
        self._preview_act = QAction("プレビュー", self)
        self._preview_act.setCheckable(True)
        # Tab はキャンバス上ではフォーカス移動より優先され、プレビュー切替として働く
        self._add_shortcut(self._preview_act, QKeySequence(Qt.Key.Key_Tab))
        self._preview_act.toggled.connect(self.canvas.set_preview_mode)
        tb.addAction(self._preview_act)
        tb.add_separator()
        # 自動検出: 専用ウィンドウでモデルと信頼度を選んでから実行する
        self._detect_act = QAction("自動検出", self)
        self._detect_act.setCheckable(True)
        self._add_shortcut(self._detect_act, QKeySequence(Qt.Key.Key_D))
        self._detect_act.toggled.connect(self._on_detect_toggled)
        tb.addAction(self._detect_act)

    def _on_block_changed(self, value: int) -> None:
        # 5px 刻みにスナップする
        snapped = max(BLOCK_STEP, round(value / BLOCK_STEP) * BLOCK_STEP)
        if snapped != value:
            self._size_slider.setValue(snapped)
            return
        self._block = snapped
        self._size_label.setText(f" {snapped}px ")
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
        if self._reject_during_detect_all() or self._reject_while_video_busy():
            return
        folder = next((p for p in paths if p.is_dir()), None)
        if folder is not None:
            self._open_folder(folder)
            return
        video = next(
            (p for p in paths if p.is_file() and video_ffmpeg.is_video_file(p)), None
        )
        if video is not None:
            self._open_video(video)
            return
        files = [p for p in paths if p.is_file() and io_utils.is_image_file(p)]
        if not files:
            QMessageBox.warning(
                self, "エラー", "対応する画像・動画ファイルが見つかりません"
            )
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
        self._leave_video_mode()
        self._folder = folder
        self._images = images
        self._index = 0
        self._store = {}
        self._load_current()

    def _add_files(self, files: list[Path]) -> None:
        """画像ファイルを編集リストの末尾へ追加し、その先頭へ表示を切り替える"""
        # 動画モード中に画像を開いたら動画は閉じる
        if self._video is not None:
            if not self._confirm_discard():
                return
            self._leave_video_mode()
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

        動画モードでは ←/→ をフレーム移動として扱う。
        """
        if self._video is not None:
            self._timeline.step(index - self._index)
            return
        if not self._images or not (0 <= index < len(self._images)):
            return
        if self._reject_during_detect_all():
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
            self._detect_window.set_image_available(
                bool(self._images) or self._video is not None
            )

    def _update_nav(self) -> None:
        if self._video is not None:
            # フレーム位置はタイムライン側に出すため、ここでは常に有効化だけ行う
            self._progress_label.setText(" 動画 ")
            self._prev_act.setEnabled(True)
            self._next_act.setEnabled(True)
            return
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
            io_utils.save_image(
                out, dest, keep_meta=not self._strip_meta_check.isChecked()
            )
        except Exception as e:
            QMessageBox.critical(self, "保存エラー", f"保存に失敗しました: {dest}\n{e}")
            return False
        self._dirty = False
        self._saved = True
        self.statusBar().showMessage(f"保存しました: {dest}", 5000)
        return True

    def _reject_during_detect_all(self) -> bool:
        """全ファイル実行中の手動操作を断る(断ったら True)

        検出はワーカーの応答待ちの間もイベントループが回るため、その間に
        画像の切替や開き直しを許すと、別の画像へ結果を書き込んでしまう。
        """
        if self._batch_models is None:
            return False
        self.statusBar().showMessage("全ファイルに検出を実行中です", 5000)
        return True

    def _save_current(self) -> None:
        if self._reject_during_detect_all() or self._reject_while_video_busy():
            return
        if self._video is not None:
            self._export_video()
            return
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

    def _on_detect_toggled(self, checked: bool) -> None:
        """ツールバーのトグルに合わせて自動検出ウィンドウを開閉する"""
        if not checked:
            if self._detect_window is not None:
                self._detect_window.close()
            return
        if not self._open_detect_window():
            # 開けなかったときはトグルを戻す(再入時は checked=False で何もしない)
            self._detect_act.setChecked(False)

    def _sync_detect_act(self) -> None:
        """ウィンドウが閉じられたらトグルの状態を合わせる"""
        self._detect_act.setChecked(False)

    def _open_detect_window(self) -> bool:
        """自動検出ウィンドウを開く(2 回目以降は前面に出す)

        推論環境が無いうちはウィンドウで何もできないため、先にセットアップを出す。
        開けたかどうかを返す。
        """
        if not detect_paths.is_runtime_ready():
            if RuntimeSetupDialog(self).exec() != QDialog.DialogCode.Accepted:
                return False
        if self._detect_window is None:
            window = DetectWindow(self._settings, self)
            window.detect_requested.connect(self._start_detect)
            window.detect_all_requested.connect(self._start_detect_all)
            window.classes_requested.connect(self._worker.request_classes)
            # モデルの顔ぶれが変わったらワーカーを畳み、次回に新しい構成で起動させる
            window.models_changed.connect(self._worker.stop)
            # ウィンドウ側の × で閉じたときもトグルを戻す
            window.finished.connect(self._sync_detect_act)
            self._detect_window = window
        self._notify_image_available()
        self._detect_window.refresh()
        self._detect_window.show()
        self._detect_window.raise_()
        self._detect_window.activateWindow()
        return True

    def _start_detect(self, models: dict) -> None:
        """表示中の画像に対して自動検出を実行する(動画モードでは全編検出)"""
        if self._video is not None:
            self._start_video_detect(models)
            return
        if not self._images or self._current_image is None or self._worker.is_busy():
            return
        if self._detect_window is not None:
            self._detect_window.set_running(True)
        self.statusBar().showMessage("検出中...")
        self._request_detect(models)

    def _request_detect(self, models: dict) -> None:
        """表示中の画像の検出をワーカーへ依頼する"""
        self._worker.request(
            str(self._images[self._index]),
            models,
            resolve_device(self._settings.device()),
        )

    def _on_detect_progress(self, done: int, total: int, _model: str) -> None:
        # 全ファイル実行中は処理済みファイル数で進捗を出すため、モデル単位の進捗は捨てる
        if self._batch_models is not None:
            return
        if self._detect_window is not None:
            self._detect_window.set_progress(done, total)

    def _finish_detect(self) -> None:
        if self._detect_window is not None:
            self._detect_window.set_running(False)

    def _add_detected_regions(self, detections: list) -> int:
        """検出結果を範囲として追加し、追加した件数を返す

        既存の範囲は残したまま、それとほぼ重なる検出だけを取り除く
        (同じ画像に検出を繰り返しても同じ範囲が積み上がらないようにする)。
        """
        if self._current_image is None:
            return 0
        regions = drop_duplicate_regions(
            detections_to_regions(detections, self._current_image.size),
            self.canvas.get_regions(),
        )
        self.canvas.add_regions(regions)
        return len(regions)

    def _on_detected(self, detections: list) -> None:
        """検出結果を範囲として追加する(既存の範囲は残す)

        全ファイル実行中は _on_batch_detected へ、動画の全編検出中は
        _on_video_frame_detected へ渡す。
        """
        if self._video_detect is not None:
            self._on_video_frame_detected(detections)
            return
        if self._batch_models is not None:
            self._on_batch_detected(detections)
            return
        self._finish_detect()
        added = self._add_detected_regions(detections)
        if not added:
            self.statusBar().showMessage("追加する範囲はありませんでした", 5000)
            return
        self.statusBar().showMessage(f"{added} 件の範囲を追加しました", 5000)

    def _on_classes_received(self, classes: dict) -> None:
        """クラス一覧が届いたら検出ウィンドウへ渡す"""
        if self._detect_window is not None:
            self._detect_window.show_class_selection(classes)

    def _on_detect_failed(self, message: str) -> None:
        # クラス一覧の取得に失敗した場合も待ち表示を畳む
        if self._detect_window is not None:
            self._detect_window.cancel_class_request()
        self._batch_models = None
        self._cleanup_video_detect()
        self._finish_detect()
        self.statusBar().clearMessage()
        QMessageBox.critical(self, "検出エラー", message)

    # --- 全ファイルへの自動検出 ---

    def _start_detect_all(self, models: dict) -> None:
        """開いている全画像に検出を行い、そのつど保存する(動画では全編検出と同じ)"""
        if self._video is not None:
            self._start_video_detect(models)
            return
        if not self._images or self._current_image is None or self._worker.is_busy():
            return
        ret = QMessageBox.question(
            self,
            "確認",
            f"{len(self._images)} 件すべての画像に自動検出を行い、保存します。\n"
            "よろしいですか?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if ret != QMessageBox.StandardButton.Yes:
            return
        self._batch_models = models
        if self._detect_window is not None:
            self._detect_window.set_running(True)
        self._detect_batch_at(0)

    def _detect_batch_at(self, index: int) -> None:
        """index の画像へ切り替えて検出を依頼する(未保存の確認はしない)"""
        # 表示中の範囲は _switch_to が保持するため、切り替えで失われない
        self._switch_to(index)
        if not self._images or self._current_image is None:
            self._finish_detect_all("対象の画像がありません")
            return
        self.statusBar().showMessage(
            f"検出中... ({self._index + 1}/{len(self._images)})"
        )
        # 進捗は「処理済みファイル数 / 全ファイル数」。この画像はこれから処理する
        if self._detect_window is not None:
            self._detect_window.set_progress(self._index, len(self._images))
        self._request_detect(self._batch_models)

    def _on_batch_detected(self, detections: list) -> None:
        """全ファイル実行中の 1 枚分の結果を反映し、保存して次へ進む"""
        self._add_detected_regions(detections)
        if not self._write_current():
            # 保存に失敗した時点で打ち切る(エラーの詳細は _write_current が表示済み)
            self._finish_detect_all(
                f"{self._index + 1} 件目の保存に失敗したため中断しました"
            )
            return
        index = self._index + 1
        if index >= len(self._images):
            self._finish_detect_all(f"{len(self._images)} 件の画像を保存しました")
            return
        self._detect_batch_at(index)

    def _finish_detect_all(self, message: str = "") -> None:
        """全ファイル実行を終える(message が空なら進捗表示を消すだけ)"""
        self._batch_models = None
        self._finish_detect()
        if message:
            self.statusBar().showMessage(message, 5000)
        else:
            self.statusBar().clearMessage()

    # --- 動画モード ---

    def _reject_while_video_busy(self) -> bool:
        """動画の書き出し・全編検出中の操作を断る(断ったら True)"""
        if self._exporter is not None:
            self.statusBar().showMessage("動画を書き出し中です", 5000)
            return True
        if self._video_detect is not None:
            self.statusBar().showMessage("動画に検出を実行中です", 5000)
            return True
        return False

    def _leave_video_mode(self) -> None:
        """動画モードを畳む(未保存確認は呼び出し側)"""
        if self._video is None:
            return
        self._video = None
        self._video_displayed_ids = set()
        self._timeline.hide()
        self._timeline.set_intervals([], None)

    def _open_video(self, path: Path) -> None:
        """動画を開いて動画モードへ切り替える"""
        if not self._confirm_discard():
            return
        if not video_ffmpeg.is_ffmpeg_ready():
            if VideoSetupDialog(self).exec() != QDialog.DialogCode.Accepted:
                return
        try:
            info = video_ffmpeg.probe(path)
        except video_ffmpeg.VideoError as e:
            QMessageBox.critical(self, "エラー", str(e))
            return
        # 画像モードの状態を畳む
        self._images = []
        self._folder = None
        self._index = 0
        self._store = {}
        self._leave_video_mode()
        self._video = VideoSession(path, info)
        self._timeline.set_range(info.frame_count)
        self._timeline.set_frame(0)
        self._timeline.show()
        self._show_frame(0)
        self._dirty = False
        self._saved = False
        self.setWindowTitle(f"{TITLE} - {path.name}")
        self._update_nav()
        self._notify_image_available()
        self.statusBar().showMessage(
            f"動画を開きました ({info.frame_count} フレーム / {info.fps:.2f} fps)", 5000
        )

    def _show_frame(self, frame: int) -> None:
        """指定フレームを取り出してキャンバスへ表示する"""
        video = self._video
        if video is None:
            return
        try:
            data = video_ffmpeg.extract_frame(video.path, frame, video.info)
            img = Image.open(io.BytesIO(data))
            img.load()
        except (video_ffmpeg.VideoError, OSError) as e:
            QMessageBox.warning(self, "読み込みエラー", f"フレームを表示できません\n{e}")
            return
        # 選択中の範囲は区間外のフレームでも表示・選択を保ち、
        # シーク後に「開始←現在」「終了←現在」で区間を広げられるようにする
        selected = [
            r for r in self.canvas.selected_regions() if video.find(r) is not None
        ]
        video.frame = frame
        self._current_image = img
        regions = video.regions_at(frame)
        shown = {id(r) for r in regions}
        regions += [r for r in selected if id(r) not in shown]
        self._video_displayed_ids = {id(r) for r in regions}
        self.canvas.set_image(img, regions)
        self.canvas.select_regions(selected)
        self.canvas.setFocus()

    def _on_frame_changed(self, frame: int) -> None:
        """タイムラインのシークに合わせて表示を切り替える"""
        if self._video is None:
            return
        # 表示中フレームでの編集を区間リストへ反映してから移動する
        self._sync_video_regions()
        self._show_frame(frame)
        self._update_selection_interval()

    def _sync_video_regions(self) -> None:
        if self._video is not None:
            self._video.sync_from_canvas(
                self.canvas.get_regions(), self._video_displayed_ids
            )

    def _update_selection_interval(self) -> None:
        """全範囲の区間と選択中の範囲をタイムラインの区間バーへ反映する"""
        video = self._video
        if video is None:
            return
        selected = self.canvas.selected_regions()
        vr = video.find(selected[0]) if len(selected) == 1 else None
        index = video.regions.index(vr) if vr is not None else None
        self._timeline.set_intervals(
            [(v.start, v.end) for v in video.regions], index
        )

    def _on_interval_edited(self, start: int, end: int) -> None:
        """区間バーの端ドラッグを選択中の範囲の区間へ反映する"""
        video = self._video
        if video is None:
            return
        selected = self.canvas.selected_regions()
        vr = video.find(selected[0]) if len(selected) == 1 else None
        if vr is None:
            return
        vr.start, vr.end = start, end
        self._dirty = True

    def _on_interval_clicked(self, index: int) -> None:
        """区間バーの帯クリックで対応する範囲を選択する"""
        video = self._video
        if video is None or not (0 <= index < len(video.regions)):
            return
        vr = video.regions[index]
        # 現在フレームに表示されていない範囲は表示に加えてから選択する
        if id(vr.region) not in self._video_displayed_ids:
            if self._current_image is None:
                return
            regions = video.regions_at(video.frame)
            if not any(r is vr.region for r in regions):
                regions.append(vr.region)
            self._video_displayed_ids = {id(r) for r in regions}
            self.canvas.set_image(self._current_image, regions)
        self.canvas.select_regions([vr.region])

    # --- 動画の書き出し ---

    def _export_video(self) -> None:
        """動画へモザイクを合成して書き出す(進捗ダイアログつき)"""
        video = self._video
        if video is None or self._exporter is not None:
            return
        self._sync_video_regions()
        dest = video_ffmpeg.mc_video_path(video.path)
        frame_paths = [
            (vr.start, vr.end, vr.region.image_path()) for vr in video.regions
        ]
        exporter = VideoExporter(
            video.path,
            dest,
            video.info,
            frame_paths,
            self._block,
            self._threshold / 100,
            self._strip_meta_check.isChecked(),
        )
        dialog = QProgressDialog(
            "動画を書き出し中...", "キャンセル", 0, video.info.frame_count, self
        )
        dialog.setWindowModality(Qt.WindowModality.WindowModal)
        dialog.setMinimumDuration(0)
        dialog.canceled.connect(exporter.cancel)
        exporter.progress.connect(self._on_export_progress)
        exporter.export_finished.connect(self._on_export_finished)
        self._exporter = exporter
        self._export_dialog = dialog
        self.statusBar().showMessage("動画を書き出し中...")
        dialog.show()
        exporter.start()

    def _on_export_progress(self, done: int, total: int) -> None:
        if self._export_dialog is not None:
            # 総フレーム数は概算のため、超えてもダイアログが先に閉じないよう抑える
            self._export_dialog.setValue(min(done, total - 1))

    def _on_export_finished(self, ok: bool, message: str) -> None:
        exporter = self._exporter
        dialog = self._export_dialog
        self._exporter = None
        self._export_dialog = None
        if dialog is not None:
            # close() でも canceled が飛ぶが、exporter を外した後なので無害
            dialog.close()
        if exporter is not None:
            exporter.wait()
        if ok:
            self._dirty = False
            self._saved = True
            self.statusBar().showMessage(message, 5000)
        elif "キャンセル" in message:
            self.statusBar().showMessage(message, 5000)
        else:
            self.statusBar().clearMessage()
            QMessageBox.critical(self, "書き出しエラー", message)

    # --- 動画への自動検出 ---

    def _start_video_detect(self, models: dict) -> None:
        """全編のフレームを取り出し、順に検出して区間つき範囲を作る"""
        video = self._video
        if video is None or self._worker.is_busy() or self._reject_while_video_busy():
            return
        step = self._timeline.detect_step()
        total = (video.info.frame_count + step - 1) // step
        ret = QMessageBox.question(
            self,
            "確認",
            f"動画の全編 (約 {total} フレーム) に自動検出を行います。\n"
            "よろしいですか?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if ret != QMessageBox.StandardButton.Yes:
            return
        tmp = Path(tempfile.mkdtemp(prefix="mosaic_vdetect_"))
        proc = QProcess(self)
        proc.finished.connect(self._on_frames_extracted)
        self._video_detect = _VideoDetectState(
            models=models, step=step, dir=tmp, proc=proc
        )
        if self._detect_window is not None:
            self._detect_window.set_running(True)
        self.statusBar().showMessage("フレームを展開中...")
        cmd = video_ffmpeg.extract_frames_command(
            video.path, video.info, step, str(tmp / "frame_%06d.jpg")
        )
        proc.start(cmd[0], cmd[1:])

    def _on_frames_extracted(self, exit_code: int, _status) -> None:
        """フレーム展開が終わったら検出のループへ入る"""
        state = self._video_detect
        if state is None:
            return
        files = sorted(state.dir.glob("frame_*.jpg"))
        if exit_code != 0 or not files:
            self._finish_video_detect("")
            QMessageBox.critical(
                self, "検出エラー", "動画からフレームを取り出せませんでした"
            )
            return
        state.files = files
        self._request_video_detect_at(0)

    def _request_video_detect_at(self, index: int) -> None:
        state = self._video_detect
        total = len(state.files)
        self.statusBar().showMessage(f"検出中... ({index + 1}/{total})")
        if self._detect_window is not None:
            self._detect_window.set_progress(index, total)
        self._worker.request(
            str(state.files[index]),
            state.models,
            resolve_device(self._settings.device()),
        )

    def _on_video_frame_detected(self, detections: list) -> None:
        """動画 1 フレーム分の検出結果を溜め、最後まで進んだらマージする"""
        state = self._video_detect
        if state is None or self._video is None:
            return
        frame = state.idx * state.step
        for det in detections:
            parsed = parse_detection(det, frame)
            if parsed is not None:
                state.dets.append(parsed)
        state.idx += 1
        if state.idx < len(state.files):
            self._request_video_detect_at(state.idx)
            return
        intervals = merge_detections(
            state.dets,
            step=state.step,
            total_frames=self._video.info.frame_count,
        )
        added = self._video.add_intervals(intervals)
        if added:
            self._dirty = True
            # 表示中フレームに掛かる範囲が増えた可能性があるため描画し直す
            self._show_frame(self._video.frame)
            self._update_selection_interval()
        self._finish_video_detect(f"{added} 件の範囲を追加しました")

    def _finish_video_detect(self, message: str) -> None:
        self._cleanup_video_detect()
        self._finish_detect()
        if message:
            self.statusBar().showMessage(message, 5000)
        else:
            self.statusBar().clearMessage()

    def _cleanup_video_detect(self) -> None:
        """動画検出の一時状態を破棄する(実行していなければ何もしない)"""
        state = self._video_detect
        if state is None:
            return
        self._video_detect = None
        if state.proc.state() != QProcess.ProcessState.NotRunning:
            state.proc.kill()
            state.proc.waitForFinished(5000)
        shutil.rmtree(state.dir, ignore_errors=True)

    # --- 未保存確認 ---

    def _on_regions_changed(self) -> None:
        self._dirty = True
        if self._video is not None:
            self._sync_video_regions()
            self._update_selection_interval()

    def _confirm_discard(self, save_unedited: bool = False) -> bool:
        """未保存の変更があれば破棄してよいか確認する

        自動保存が有効なときは確認せず保存して続行する。
        save_unedited が True なら、無編集のまま離れる場合も保存する。
        動画の書き出しは重いため、自動保存の対象にはしない。
        """
        if self._autosave_check.isChecked() and self._video is None:
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
            if self._exporter is not None:
                self._exporter.cancel()
                self._exporter.wait()
            self._cleanup_video_detect()
            if self._detect_window is not None:
                self._detect_window.close()
            self._worker.stop()
            self._settings.set_geometry(self.saveGeometry())
            event.accept()
        else:
            event.ignore()
