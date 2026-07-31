"""動画モードのタイムライン UI(シーク・再生操作)

区間の表示と編集はタイムラインウィンドウ(video/timeline_window.py)が担い、
検出の条件は検出範囲ダイアログ(video/detect_range_dialog.py)が持つ。
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QPoint, Qt, Signal
from PySide6.QtGui import QGuiApplication, QImage, QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from mosaic_tool.video.timecode import format_timecode

# 再生ボタンはスタイル標準のメディアアイコンを使う。
# ▶/⏸ の文字は環境によって絵文字字形(カラー)で描かれ、周りのボタンから浮く
PLAY_ICON = QStyle.StandardPixmap.SP_MediaPlay
PAUSE_ICON = QStyle.StandardPixmap.SP_MediaPause

# 再生速度の選択肢(倍率)。既定は 1.0
SPEEDS = (0.25, 0.5, 1.0, 2.0)
DEFAULT_SPEED = 1.0

# ホバープレビューをスライダーの上へ浮かせる距離 (px)
PREVIEW_GAP = 8


class SeekPreview(QWidget):
    """スライダーのホバー位置に出すサムネイルのポップアップ"""

    def __init__(self, parent=None):
        super().__init__(parent, Qt.WindowType.ToolTip)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(0)
        self.image = QLabel()
        self.image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.image)
        self.caption = QLabel()
        self.caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.caption)

    def popup(
        self, anchor: QPoint, pixmap: QPixmap, frame: int, time: str
    ) -> None:
        """anchor(グローバル座標)の真上へサムネイルとフレーム番号・時刻を出す"""
        self.image.setPixmap(pixmap)
        self.caption.setText(f"{frame}  {time}")
        self.adjustSize()
        x = anchor.x() - self.width() // 2
        y = anchor.y() - self.height() - PREVIEW_GAP
        # 画面端でははみ出して見切れるので、表示領域内へ寄せる
        screen = QGuiApplication.screenAt(anchor) or QGuiApplication.primaryScreen()
        if screen is not None:
            area = screen.availableGeometry()
            x = min(max(x, area.left()), area.right() - self.width() + 1)
            y = min(max(y, area.top()), area.bottom() - self.height() + 1)
        self.move(x, y)
        self.show()


class TimelineBar(QWidget):
    """キャンバス下に出すタイムライン。動画モードのときだけ表示する"""

    frame_changed = Signal(int)   # シークやコマ送りでフレームが変わった
    play_clicked = Signal()       # 再生/一時停止が押された(実際の開始・停止は app 側)
    speed_changed = Signal(float)  # 再生速度が変わった

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        # 時刻表記に使う fps(動画を開くまでは 0 = 00:00.00 表示)
        self._fps = 0.0
        self._prev_btn = QPushButton("◀")
        self._prev_btn.setFixedWidth(28)
        self._prev_btn.clicked.connect(lambda: self.step(-1))
        layout.addWidget(self._prev_btn)
        self._slider = QSlider(Qt.Orientation.Horizontal)
        self._slider.setRange(0, 0)
        self._slider.valueChanged.connect(self.frame_changed)
        self._slider.valueChanged.connect(lambda _: self._update_label())
        # ホバー位置のサムネイルプレビュー(サムネイルは app 側が流し込む)
        self._slider.setMouseTracking(True)
        self._slider.installEventFilter(self)
        self._thumbs: dict[int, QPixmap] = {}
        self._preview = SeekPreview(self)
        layout.addWidget(self._slider, 1)
        self._next_btn = QPushButton("▶|")
        self._next_btn.setFixedWidth(28)
        self._next_btn.clicked.connect(lambda: self.step(1))
        layout.addWidget(self._next_btn)
        self._time_label = QLabel()
        self._update_label()
        layout.addWidget(self._time_label)
        # 再生ボタンは Space のショートカットと二重に効かないようフォーカスを持たせない
        self._playing = False
        self._play_btn = QPushButton()
        self._play_btn.setFixedWidth(28)
        self._play_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._play_btn.clicked.connect(self.play_clicked)
        self.set_playing(False)
        layout.addWidget(self._play_btn)
        layout.addWidget(QLabel(" 速度 "))
        self._speed_combo = QComboBox()
        for speed in SPEEDS:
            self._speed_combo.addItem(f"{speed}x", speed)
        self._speed_combo.setCurrentIndex(SPEEDS.index(DEFAULT_SPEED))
        self._speed_combo.currentIndexChanged.connect(
            lambda _: self.speed_changed.emit(self.speed())
        )
        layout.addWidget(self._speed_combo)

    def eventFilter(self, obj, event) -> bool:
        if obj is self._slider:
            if event.type() == QEvent.Type.MouseMove:
                self._show_preview_at(event.position().x())
            elif event.type() in (QEvent.Type.Leave, QEvent.Type.Hide):
                self._preview.hide()
        return super().eventFilter(obj, event)

    def add_thumbnail(self, frame: int, image: QImage) -> None:
        """ホバープレビュー用のサムネイルを受け取る(生成できた分から届く)"""
        self._thumbs[frame] = QPixmap.fromImage(image)

    def clear_thumbnails(self) -> None:
        self._thumbs.clear()
        self._preview.hide()

    def _nearest_thumb_frame(self, frame: int) -> int | None:
        """frame に最も近いサムネイル済みフレーム。まだ 1 枚も無ければ None"""
        if not self._thumbs:
            return None
        return min(self._thumbs, key=lambda k: abs(k - frame))

    def _show_preview_at(self, x: float) -> None:
        """スライダー上の x 位置に対応するフレームのプレビューを出す"""
        frame = QStyle.sliderValueFromPosition(
            self._slider.minimum(),
            self._slider.maximum(),
            round(x),
            self._slider.width(),
        )
        nearest = self._nearest_thumb_frame(frame)
        if nearest is None:
            self._preview.hide()
            return
        anchor = self._slider.mapToGlobal(QPoint(round(x), 0))
        self._preview.popup(
            anchor, self._thumbs[nearest], frame, self._timecode(frame)
        )

    def set_range(self, total_frames: int, fps: float) -> None:
        """fps は時刻表記に使う"""
        self._fps = fps
        self._slider.setRange(0, max(0, total_frames - 1))
        self._update_label()

    def set_frame(self, frame: int) -> None:
        """表示位置を合わせる(frame_changed は発火させない)"""
        self._slider.blockSignals(True)
        self._slider.setValue(frame)
        self._slider.blockSignals(False)
        self._update_label()

    def frame(self) -> int:
        return self._slider.value()

    def seek(self, frame: int) -> None:
        """外部(タイムラインウィンドウ)からのシーク。frame_changed を発火する"""
        self._slider.setValue(frame)

    def step(self, delta: int) -> None:
        self._slider.setValue(self._slider.value() + delta)

    def set_playing(self, playing: bool) -> None:
        """再生中かどうかをボタンの表示へ反映する"""
        self._playing = playing
        self._play_btn.setIcon(
            self.style().standardIcon(PAUSE_ICON if playing else PLAY_ICON)
        )

    def is_playing(self) -> bool:
        """ボタンが示している再生状態(再生 UI が続いているか)"""
        return self._playing

    def speed(self) -> float:
        return float(self._speed_combo.currentData())

    def _timecode(self, frame: int) -> str:
        return format_timecode(frame, self._fps)

    def _update_label(self) -> None:
        """現在位置と全体を時刻で出す"""
        frame = self._slider.value()
        last = self._slider.maximum()
        self._time_label.setText(
            f" {self._timecode(frame)} / {self._timecode(last)} "
        )
