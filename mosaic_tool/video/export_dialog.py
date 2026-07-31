"""動画書き出しの設定(フォーマット・解像度・品質)を決めるダイアログ"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QSlider,
    QVBoxLayout,
)

from mosaic_tool.settings import AppSettings
from mosaic_tool.video import ffmpeg
from mosaic_tool.video.ffmpeg import ExportSettings, VideoInfo

# コーデックの表示名。互換性と圧縮率のトレードオフを一言で添える
_CODECS = (
    ("H.264 (互換性重視)", "h264"),
    ("H.265 (高圧縮)", "h265"),
    ("無圧縮 AVI (最高画質・巨大)", "rawvideo"),
)


class ExportDialog(QDialog):
    """書き出し前に設定を確認するモーダルダイアログ

    前回の選択を AppSettings から復元し、OK で保存する。
    """

    def __init__(self, info: VideoInfo, settings: AppSettings, parent=None):
        super().__init__(parent)
        self._settings = settings
        self.setWindowTitle("動画の書き出し")

        self._codec = QComboBox()
        for label, value in _CODECS:
            self._codec.addItem(label, value)
        index = self._codec.findData(settings.video_codec())
        self._codec.setCurrentIndex(max(0, index))
        # CRF はコーデックごとに保持し、切り替えでレンジと値を入れ替える
        # (無圧縮は品質を選べないため持たない)
        self._crf_by_codec = {
            value: settings.video_crf(value)
            for _, value in _CODECS
            if not ffmpeg.is_lossless(value)
        }
        self._codec.currentIndexChanged.connect(self._on_codec_changed)

        # 元動画の短辺以上のプリセットは縮小にならないため出さない
        self._resolution = QComboBox()
        self._resolution.addItem("元のサイズ", 0)
        short = min(info.width, info.height)
        for side in ffmpeg.EXPORT_SHORT_SIDES:
            if side < short:
                self._resolution.addItem(f"{side}p", side)
        index = self._resolution.findData(settings.video_resolution())
        # 保存値がこの動画のプリセットに無い場合は「元のサイズ」へ表示だけ
        # フォールバックする(保存値の扱いは accept を参照)
        self._resolution_fallback = index < 0
        self._resolution.setCurrentIndex(max(0, index))

        # スライダーの値は CRF そのもの(左=小さい値=高品質)
        self._crf = QSlider(Qt.Orientation.Horizontal)
        self._crf_label = QLabel()
        self._crf_caption = QLabel("品質")
        self._crf.valueChanged.connect(self._update_crf_label)
        crf_row = QHBoxLayout()
        crf_row.addWidget(self._crf)
        crf_row.addWidget(self._crf_label)

        grid = QGridLayout()
        grid.addWidget(QLabel("フォーマット"), 0, 0)
        grid.addWidget(self._codec, 0, 1)
        grid.addWidget(QLabel("解像度"), 1, 0)
        grid.addWidget(self._resolution, 1, 1)
        grid.addWidget(self._crf_caption, 2, 0)
        grid.addLayout(crf_row, 2, 1)
        grid.setColumnStretch(1, 1)
        # 品質行はウィジェットを載せてから状態を決める(無圧縮なら隠す)
        self._apply_codec_crf()

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(grid)
        layout.addWidget(buttons)

    def _current_codec(self) -> str:
        return str(self._codec.currentData())

    def _apply_codec_crf(self) -> None:
        """現在のコーデックの CRF レンジと保持値をスライダーへ反映する

        無圧縮では品質を選べないため、行ごと隠す。
        """
        codec = self._current_codec()
        lossless = ffmpeg.is_lossless(codec)
        for widget in (self._crf_caption, self._crf, self._crf_label):
            widget.setVisible(not lossless)
        if not lossless:
            minimum, maximum = ffmpeg.crf_range(codec)
            self._crf.setRange(minimum, maximum)
            self._crf.setValue(self._crf_by_codec[codec])
            self._update_crf_label()
        self._previous_codec = codec  # 次回の切り替えで値を保持するため覚えておく
        # 隠した行の分だけ縮める(表示に戻したときも高さを取り直す)
        self.adjustSize()

    def _on_codec_changed(self) -> None:
        """コーデック切り替え。直前のコーデックの CRF は保持したままにする"""
        if not ffmpeg.is_lossless(self._previous_codec):
            self._crf_by_codec[self._previous_codec] = self._crf.value()
        self._apply_codec_crf()

    def _update_crf_label(self) -> None:
        self._crf_label.setText(f"CRF {self._crf.value()}")

    def export_settings(self) -> ExportSettings:
        """現在の選択内容。解像度 0 (元のサイズ) は None へ変換する

        無圧縮は CRF を使わないため、スライダーの残り値ではなく既定値を入れる。
        """
        side = int(self._resolution.currentData())
        codec = self._current_codec()
        lossless = ffmpeg.is_lossless(codec)
        crf = ffmpeg.crf_default(codec) if lossless else self._crf.value()
        return ExportSettings(
            codec=codec,
            max_short_side=side or None,
            crf=crf,
        )

    def accept(self) -> None:
        """OK 時に選択を保存してから閉じる(次回の初期値になる)

        解像度だけは、保存値がプリセットに無くフォールバック表示のまま
        触られていない場合に保存をスキップする。小さい動画を 1 本挟んだだけで
        保存済みの 1080p などが消えてしまうのを防ぐ。
        """
        result = self.export_settings()
        self._settings.set_video_codec(result.codec)
        untouched_fallback = (
            self._resolution_fallback and result.max_short_side is None
        )
        if not untouched_fallback:
            self._settings.set_video_resolution(result.max_short_side or 0)
        # CRF はコーデックごとに保存する(切り替えて戻したときに値が残るように)
        if not ffmpeg.is_lossless(result.codec):
            self._crf_by_codec[result.codec] = result.crf
        for codec, crf in self._crf_by_codec.items():
            self._settings.set_video_crf(codec, crf)
        super().accept()
