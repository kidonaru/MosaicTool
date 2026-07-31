# 動画書き出しダイアログ Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 動画保存の実行前に専用ダイアログを表示し、フォーマット (H.264/H.265)・解像度 (短辺プリセット)・品質 (スライダー) を指定できるようにする。

**Architecture:** `ffmpeg.py` に GUI 非依存の `ExportSettings` dataclass と純粋関数 (CRF マッピング・出力サイズ計算) を追加し、`encode_command()` を拡張。新規 `video/export_dialog.py` がダイアログ UI を担い、`app.py` の `_export_video()` が書き出し前にダイアログを挟む。設定は `AppSettings` (QSettings ラッパー) で永続化する。

**Tech Stack:** Python 3.11+ / PySide6 / ffmpeg (subprocess) / pytest

**Spec:** `docs/superpowers/specs/2026-07-31-video-export-dialog-design.md`

## Global Constraints

- コードのコメント・エラーメッセージ・テスト docstring は日本語で書く
- 出力先パスは `元名_mc.mp4` 固定のまま変更しない
- メタ削除はツールバーのチェックボックスのまま (ダイアログへ移さない)
- 新規依存ライブラリは追加しない
- テスト実行: `python -m pytest tests/<file> -v` (GUI テストは `QT_QPA_PLATFORM=offscreen`、conftest.py が設定済み)
- 既存の書き出し品質のデフォルト (H.264 / CRF 18 / 元サイズ) を初期値として維持する

---

### Task 1: ffmpeg.py — ExportSettings と純粋関数、encode_command 拡張

**Files:**
- Modify: `mosaic_tool/video/ffmpeg.py` (定数 `ENCODE_CRF` 周辺と `encode_command()`)
- Test: `tests/test_video_ffmpeg.py`

**Interfaces:**
- Produces:
  - `ExportSettings(codec: str = "h264", max_short_side: int | None = None, quality: int = 70)` — frozen dataclass。`codec` は `"h264"` | `"h265"`、`max_short_side` は短辺上限 px (None = 元のサイズ)、`quality` は 0〜100
  - `EXPORT_SHORT_SIDES = (1080, 720, 480)` — 解像度プリセット (短辺上限)
  - `EXPORT_QUALITY_DEFAULT = 70` — 品質スライダー既定値 (H.264 CRF 18 相当)
  - `crf_for(codec: str, quality: int) -> int`
  - `export_size(info: VideoInfo, max_short_side: int | None) -> tuple[int, int]`
  - `encode_command(src, dest, info, *, strip_meta: bool, export: ExportSettings) -> list[str]` — `export` キーワード引数を追加 (デフォルトなし・必須)

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_video_ffmpeg.py` の `TestCommands` クラスの後に追加する
(既存の `info` fixture は 1280x720 / aac):

```python
class TestCrfFor:
    def test_h264_range(self):
        # 低容量 28 〜 高品質 14 の線形マッピング
        assert ffmpeg.crf_for("h264", 0) == 28
        assert ffmpeg.crf_for("h264", 100) == 14

    def test_h265_range(self):
        # H.265 は同品質の CRF が高めのためレンジをずらす
        assert ffmpeg.crf_for("h265", 0) == 32
        assert ffmpeg.crf_for("h265", 100) == 18

    def test_default_quality_keeps_current_h264_crf(self):
        # 既定スライダー位置は従来の書き出し品質 (CRF 18) と一致させる
        assert ffmpeg.crf_for("h264", ffmpeg.EXPORT_QUALITY_DEFAULT) == 18

    def test_unknown_codec_falls_back_to_h264(self):
        # encode_command のコーデック選択と同じく未知値は h264 扱いにする
        assert ffmpeg.crf_for("av1", 100) == 14


class TestExportSize:
    def _info(self, w, h):
        return ffmpeg.VideoInfo(w, h, 30.0, "30/1", 90, 3.0, None)

    def test_no_limit_keeps_original(self):
        assert ffmpeg.export_size(self._info(1920, 1080), None) == (1920, 1080)

    def test_shrinks_by_short_side(self):
        # 4K → 1080p は短辺 2160 → 1080 の半分に縮む
        assert ffmpeg.export_size(self._info(3840, 2160), 1080) == (1920, 1080)

    def test_portrait_uses_short_side(self):
        # 縦動画の短辺は横幅。1080p 指定でも短辺 1080 なら無変換
        assert ffmpeg.export_size(self._info(1080, 1920), 1080) == (1080, 1920)

    def test_never_upscales(self):
        assert ffmpeg.export_size(self._info(640, 360), 1080) == (640, 360)

    def test_rounds_to_even(self):
        # yuv420p が奇数サイズを扱えないため偶数へ切り詰める
        assert ffmpeg.export_size(self._info(101, 57), None) == (100, 56)


class TestEncodeExportSettings:
    def _cmd(self, info, export):
        return ffmpeg.encode_command(
            Path("in.mp4"), Path("out.mp4"), info, strip_meta=False, export=export
        )

    def test_h264_defaults(self, info):
        cmd = self._cmd(info, ffmpeg.ExportSettings())
        assert cmd[cmd.index("-c:v") + 1] == "libx264"
        assert cmd[cmd.index("-crf") + 1] == "18"
        assert "-tag:v" not in cmd
        assert "-preset" not in cmd

    def test_h265_uses_libx265_with_hvc1_tag(self, info):
        # hvc1 タグは Apple 系プレイヤーの互換性のため
        cmd = self._cmd(info, ffmpeg.ExportSettings(codec="h265"))
        assert cmd[cmd.index("-c:v") + 1] == "libx265"
        assert cmd[cmd.index("-tag:v") + 1] == "hvc1"
        # 既定品質 70 は round(32 - 14 * 0.7) = 22
        assert cmd[cmd.index("-crf") + 1] == "22"
        # x265 の既定 preset (medium) は極端に遅く、エンコーダ待ちの
        # タイムアウト(ENCODER_WAIT)に届きうるため速度優先にする
        assert cmd[cmd.index("-preset") + 1] == "fast"

    def test_scale_uses_computed_size(self):
        uhd = ffmpeg.VideoInfo(3840, 2160, 30.0, "30/1", 90, 3.0, None)
        cmd = self._cmd(uhd, ffmpeg.ExportSettings(max_short_side=1080))
        assert cmd[cmd.index("-vf") + 1] == "scale=1920:1080"

    def test_original_size_still_rounds_to_even(self):
        odd = ffmpeg.VideoInfo(101, 57, 30.0, "30/1", 90, 3.0, None)
        cmd = self._cmd(odd, ffmpeg.ExportSettings())
        assert cmd[cmd.index("-vf") + 1] == "scale=100:56"
```

あわせて既存の `TestCommands` 内の `encode_command` 呼び出し 4 件
(`test_encode_copies_compatible_audio` / `test_encode_reencodes_incompatible_audio` /
`test_encode_without_audio` / `test_encode_strip_meta`) に
`export=ffmpeg.ExportSettings()` を追加する。

- [ ] **Step 2: テストが失敗することを確認する**

Run: `python -m pytest tests/test_video_ffmpeg.py -v`
Expected: FAIL (`AttributeError: ... has no attribute 'crf_for'` など)

- [ ] **Step 3: 実装する**

`mosaic_tool/video/ffmpeg.py` の `ENCODE_CRF = 18` を以下に置き換える:

```python
# 書き出し設定のプリセット。品質スライダー (0〜100) は CRF へ線形マッピングする。
# 同じ見た目の品質でも H.265 は CRF が高めになるため、コーデックごとにレンジを持つ
EXPORT_SHORT_SIDES = (1080, 720, 480)  # 解像度プリセット (短辺上限 px)
EXPORT_QUALITY_DEFAULT = 70            # 従来の H.264 CRF 18 相当
_CRF_RANGES = {"h264": (28, 14), "h265": (32, 18)}  # (低容量, 高品質)
```

`VideoInfo` の後 (VideoError の前) に追加:

```python
@dataclass(frozen=True)
class ExportSettings:
    """書き出しダイアログで選ぶ設定。既定値は従来の書き出し (H.264 / CRF 18 / 元サイズ)"""

    codec: str = "h264"                # "h264" | "h265"
    max_short_side: int | None = None  # 短辺の上限 px。None は元のサイズ
    quality: int = EXPORT_QUALITY_DEFAULT  # 0 (低容量) 〜 100 (高品質)


def crf_for(codec: str, quality: int) -> int:
    """品質スライダー値 (0〜100) をコーデックに応じた CRF へ変換する

    未知のコーデックは encode_command のフォールバックと揃えて h264 扱いにする。
    """
    low, high = _CRF_RANGES.get(codec, _CRF_RANGES["h264"])
    return round(low - (low - high) * quality / 100)


def export_size(info: VideoInfo, max_short_side: int | None) -> tuple[int, int]:
    """書き出しの出力サイズ。短辺を上限に縮小のみ行い、偶数へ丸める

    偶数へ丸めるのは yuv420p が奇数サイズを扱えないため (縮小なしでも適用する)。
    """
    short = min(info.width, info.height)
    if max_short_side is None or short <= max_short_side:
        width, height = info.width, info.height
    else:
        width = round(info.width * max_short_side / short)
        height = round(info.height * max_short_side / short)
    return max(2, width - width % 2), max(2, height - height % 2)
```

`encode_command()` を変更する:

```python
def encode_command(
    src: Path, dest: Path, info: VideoInfo, *, strip_meta: bool, export: ExportSettings
) -> list[str]:
    """標準入力の rawvideo をエンコードし、音声を元動画から引き継ぐコマンド"""
    cmd = [
        str(ffmpeg_path()), "-y", "-v", "error",
        "-f", "rawvideo", "-pix_fmt", "rgb24",
        "-s", f"{info.width}x{info.height}", "-r", info.fps_expr, "-i", "-",
        "-i", str(src),
        "-map", "0:v",
    ]
    if info.audio_codec is not None:
        cmd += ["-map", "1:a"]
    codec = "libx265" if export.codec == "h265" else "libx264"
    width, height = export_size(info, export.max_short_side)
    cmd += [
        "-c:v", codec, "-pix_fmt", "yuv420p",
        "-crf", str(crf_for(export.codec, export.quality)),
        "-vf", f"scale={width}:{height}",
    ]
    if export.codec == "h265":
        # hvc1: Apple 系プレイヤーで再生できるようにするタグ。
        # preset fast: x265 の既定 (medium) は極端に遅く、エンコーダ待ちの
        # タイムアウト(exporter.ENCODER_WAIT)に届きうるため速度優先にする
        cmd += ["-tag:v", "hvc1", "-preset", "fast"]
    if info.audio_codec is not None:
        if info.audio_codec in MP4_COPY_AUDIO:
            cmd += ["-c:a", "copy"]
        else:
            cmd += ["-c:a", "aac", "-b:a", "192k"]
    # 既定でもグローバルメタデータは 2 入力目から引き継がれないため明示する
    cmd += ["-map_metadata", "-1" if strip_meta else "1"]
    cmd += ["-movflags", "+faststart", str(dest)]
    return cmd
```

(従来の `-vf scale=trunc(iw/2)*2:trunc(ih/2)*2` は `export_size` の偶数丸めが
置き換えるので削除する)

- [ ] **Step 4: テストが通ることを確認する**

Run: `python -m pytest tests/test_video_ffmpeg.py -v`
Expected: PASS (全件)

- [ ] **Step 5: exporter.py と app.py の呼び出しを追従させる**

`mosaic_tool/video/exporter.py` の `VideoExporter.__init__` に `export` 引数を追加し、
`encode_command` へ渡す:

```python
    def __init__(
        self,
        src: Path,
        dest: Path,
        info: VideoInfo,
        frame_paths: list[tuple[int, int, QPainterPath]],
        block: int,
        threshold: float,
        strip_meta: bool,
        export: ffmpeg.ExportSettings,
    ):
```

`self._export = export` を保存し、`run()` 内の `ffmpeg.encode_command(...)` 呼び出しを
以下にする (`from mosaic_tool.video.ffmpeg import VideoInfo` は既存 import のままでよい):

```python
                ffmpeg.encode_command(
                    self._src, self._dest, self._info,
                    strip_meta=self._strip_meta, export=self._export,
                ),
```

`run()` の `except` 節の失敗メッセージに H.265 のヒントを添える
(ffmpeg ビルドに libx265 が無い場合、stderr を捨てているため原因が伝わらない):

```python
            else:
                message = f"書き出しに失敗しました: {e}"
                if self._export.codec == "h265":
                    # エンコーダ不在でも詳細が残らないため、可能性として案内する
                    message += (
                        "\n(ご利用の ffmpeg が H.265 エンコードに"
                        "対応していない可能性があります)"
                    )
                self.export_finished.emit(False, message)
```

あわせて `mosaic_tool/app.py:1110` 付近の `VideoExporter(...)` 呼び出しの末尾に
暫定で既定設定を渡し、このコミット時点でも動画保存が壊れないようにする
(Task 4 でダイアログの選択値に差し替える):

```python
            self._strip_meta_check.isChecked(),
            video_ffmpeg.ExportSettings(),
        )
```

Run: `python -m pytest tests/test_video_ffmpeg.py -v`
Expected: PASS (全件)

- [ ] **Step 6: コミット**

```bash
git add mosaic_tool/video/ffmpeg.py mosaic_tool/video/exporter.py mosaic_tool/app.py tests/test_video_ffmpeg.py
git commit -m "feat(video): 書き出し設定 (コーデック・解像度・品質) を encode_command に追加"
```

---

### Task 2: settings.py — 書き出し設定の永続化

**Files:**
- Modify: `mosaic_tool/settings.py`
- Test: `tests/test_settings.py`

**Interfaces:**
- Consumes: `mosaic_tool.video.ffmpeg` の `EXPORT_SHORT_SIDES`, `EXPORT_QUALITY_DEFAULT` (Task 1)
- Produces:
  - `AppSettings.video_codec() -> str` / `set_video_codec(value: str)` — `"h264"` | `"h265"`、不正値は `"h264"`
  - `AppSettings.video_resolution() -> int` / `set_video_resolution(value: int)` — 短辺上限 px。`0` は元のサイズ。許可値は `0` と `EXPORT_SHORT_SIDES`、不正値は `0`
  - `AppSettings.video_quality() -> int` / `set_video_quality(value: int)` — 0〜100、不正値は `EXPORT_QUALITY_DEFAULT`

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_settings.py` の末尾に追加する:

```python
def test_video_export_defaults(tmp_path):
    s = _settings(tmp_path)
    assert s.video_codec() == "h264"
    assert s.video_resolution() == 0
    assert s.video_quality() == 70


def test_video_export_roundtrip(tmp_path):
    s = _settings(tmp_path)
    s.set_video_codec("h265")
    s.set_video_resolution(720)
    s.set_video_quality(85)
    s2 = _settings(tmp_path)
    assert s2.video_codec() == "h265"
    assert s2.video_resolution() == 720
    assert s2.video_quality() == 85


def test_video_export_invalid_values_fall_back(tmp_path):
    """手動編集などで壊れた値は既定値へ戻す"""
    s = _settings(tmp_path)
    s._qsettings.setValue("video/codec", "av1")
    s._qsettings.setValue("video/resolution", 999)
    s._qsettings.setValue("video/quality", 150)
    assert s.video_codec() == "h264"
    assert s.video_resolution() == 0
    assert s.video_quality() == 70
```

- [ ] **Step 2: テストが失敗することを確認する**

Run: `python -m pytest tests/test_settings.py -v`
Expected: FAIL (`AttributeError: 'AppSettings' object has no attribute 'video_codec'`)

- [ ] **Step 3: 実装する**

`mosaic_tool/settings.py` に追加する。import に以下を足す:

```python
from mosaic_tool.video.ffmpeg import EXPORT_QUALITY_DEFAULT, EXPORT_SHORT_SIDES
```

既定値ブロックへ追加:

```python
DEFAULT_VIDEO_CODEC = "h264"  # 動画書き出しコーデック ("h264" / "h265")
DEFAULT_VIDEO_RESOLUTION = 0  # 動画書き出しの短辺上限 px (0 は元のサイズ)
```

キー定義へ追加:

```python
_KEY_VIDEO_CODEC = "video/codec"
_KEY_VIDEO_RESOLUTION = "video/resolution"
_KEY_VIDEO_QUALITY = "video/quality"
```

`AppSettings` にメソッドを追加 (`# --- ウィンドウジオメトリ ---` の前):

```python
    # --- 動画書き出し ---

    def video_codec(self) -> str:
        value = str(self._qsettings.value(_KEY_VIDEO_CODEC, DEFAULT_VIDEO_CODEC))
        return value if value in ("h264", "h265") else DEFAULT_VIDEO_CODEC

    def set_video_codec(self, value: str) -> None:
        self._qsettings.setValue(_KEY_VIDEO_CODEC, value)

    def video_resolution(self) -> int:
        """書き出しの短辺上限 px。0 は元のサイズ。プリセット外の値は既定値へ戻す"""
        try:
            value = int(
                self._qsettings.value(_KEY_VIDEO_RESOLUTION, DEFAULT_VIDEO_RESOLUTION)
            )
        except (TypeError, ValueError):
            return DEFAULT_VIDEO_RESOLUTION
        allowed = (DEFAULT_VIDEO_RESOLUTION, *EXPORT_SHORT_SIDES)
        return value if value in allowed else DEFAULT_VIDEO_RESOLUTION

    def set_video_resolution(self, value: int) -> None:
        self._qsettings.setValue(_KEY_VIDEO_RESOLUTION, int(value))

    def video_quality(self) -> int:
        return self._int(_KEY_VIDEO_QUALITY, EXPORT_QUALITY_DEFAULT, 0, 100)

    def set_video_quality(self, value: int) -> None:
        self._qsettings.setValue(_KEY_VIDEO_QUALITY, int(value))
```

- [ ] **Step 4: テストが通ることを確認する**

Run: `python -m pytest tests/test_settings.py -v`
Expected: PASS (全件)

- [ ] **Step 5: コミット**

```bash
git add mosaic_tool/settings.py tests/test_settings.py
git commit -m "feat(settings): 動画書き出し設定 (コーデック・解像度・品質) を永続化する"
```

---

### Task 3: export_dialog.py — 書き出し設定ダイアログ

**Files:**
- Create: `mosaic_tool/video/export_dialog.py`
- Test: `tests/test_video_export_dialog.py`

**Interfaces:**
- Consumes:
  - `ffmpeg.ExportSettings`, `ffmpeg.EXPORT_SHORT_SIDES`, `ffmpeg.VideoInfo` (Task 1)
  - `AppSettings.video_codec()` ほか読み書きメソッド (Task 2)
- Produces:
  - `ExportDialog(info: VideoInfo, settings: AppSettings, parent=None)` — QDialog。
    accept 時に選択値を `AppSettings` へ保存する
  - `ExportDialog.export_settings() -> ExportSettings` — 選択内容 (解像度 0 は
    `max_short_side=None` へ変換)

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_video_export_dialog.py` を新規作成する:

```python
"""動画書き出しダイアログ(初期値の復元・プリセット絞り込み・設定保存)の検証"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from mosaic_tool.settings import AppSettings  # noqa: E402
from mosaic_tool.video import ffmpeg  # noqa: E402
from mosaic_tool.video.export_dialog import ExportDialog  # noqa: E402


def _settings(tmp_path) -> AppSettings:
    path = tmp_path / "mosaic_tool.ini"
    return AppSettings(QSettings(str(path), QSettings.Format.IniFormat))


def _info(w=3840, h=2160):
    return ffmpeg.VideoInfo(w, h, 30.0, "30/1", 90, 3.0, None)


def make_dialog(tmp_path, info=None, settings=None):
    QApplication.instance() or QApplication([])
    return ExportDialog(info or _info(), settings or _settings(tmp_path))


class TestDefaults:
    def test_initial_selection_matches_defaults(self, tmp_path):
        d = make_dialog(tmp_path)
        result = d.export_settings()
        assert result == ffmpeg.ExportSettings()

    def test_restores_saved_settings(self, tmp_path):
        s = _settings(tmp_path)
        s.set_video_codec("h265")
        s.set_video_resolution(720)
        s.set_video_quality(85)
        d = make_dialog(tmp_path, settings=s)
        assert d.export_settings() == ffmpeg.ExportSettings("h265", 720, 85)


class TestResolutionPresets:
    def test_hides_presets_at_or_above_the_short_side(self, tmp_path):
        # 1920x1080 (短辺 1080) では 1080p は無意味なので出さない
        d = make_dialog(tmp_path, info=_info(1920, 1080))
        values = [
            d._resolution.itemData(i) for i in range(d._resolution.count())
        ]
        assert values == [0, 720, 480]

    def test_portrait_uses_width_as_short_side(self, tmp_path):
        # 縦動画 1080x1920 の短辺は 1080
        d = make_dialog(tmp_path, info=_info(1080, 1920))
        values = [
            d._resolution.itemData(i) for i in range(d._resolution.count())
        ]
        assert values == [0, 720, 480]

    def test_saved_resolution_missing_from_presets_falls_back(self, tmp_path):
        # 前回 1080p を選んでいても、短辺 720 の動画では選択肢にないため元のサイズへ
        s = _settings(tmp_path)
        s.set_video_resolution(1080)
        d = make_dialog(tmp_path, info=_info(1280, 720), settings=s)
        assert d.export_settings().max_short_side is None


class TestAccept:
    def test_accept_saves_to_settings(self, tmp_path):
        s = _settings(tmp_path)
        d = make_dialog(tmp_path, settings=s)
        d._codec.setCurrentIndex(d._codec.findData("h265"))
        d._quality.setValue(30)
        d.accept()
        assert s.video_codec() == "h265"
        assert s.video_quality() == 30

    def test_accept_keeps_saved_resolution_when_preset_is_hidden(self, tmp_path):
        # 短辺の小さい動画でフォールバック表示になっただけなら、保存済みの
        # 1080p を 0 で上書きしない(次に大きい動画を開いたとき復元するため)
        s = _settings(tmp_path)
        s.set_video_resolution(1080)
        d = make_dialog(tmp_path, info=_info(1280, 720), settings=s)
        d.accept()
        assert s.video_resolution() == 1080

    def test_accept_saves_an_explicit_resolution_choice(self, tmp_path):
        # フォールバック表示でも、ユーザーが明示的に選び直した値は保存する
        s = _settings(tmp_path)
        s.set_video_resolution(1080)
        d = make_dialog(tmp_path, info=_info(1280, 720), settings=s)
        d._resolution.setCurrentIndex(d._resolution.findData(480))
        d.accept()
        assert s.video_resolution() == 480
```

- [ ] **Step 2: テストが失敗することを確認する**

Run: `python -m pytest tests/test_video_export_dialog.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'mosaic_tool.video.export_dialog'`)

- [ ] **Step 3: 実装する**

`mosaic_tool/video/export_dialog.py` を新規作成する:

```python
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
_CODECS = (("H.264 (互換性重視)", "h264"), ("H.265 (高圧縮)", "h265"))


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

        # 品質は CRF へマッピングするスライダー(数値の意味を出さず両端ラベルで示す)
        self._quality = QSlider(Qt.Orientation.Horizontal)
        self._quality.setRange(0, 100)
        self._quality.setValue(settings.video_quality())
        quality_row = QHBoxLayout()
        quality_row.addWidget(QLabel("低容量"))
        quality_row.addWidget(self._quality)
        quality_row.addWidget(QLabel("高品質"))

        grid = QGridLayout()
        grid.addWidget(QLabel("フォーマット"), 0, 0)
        grid.addWidget(self._codec, 0, 1)
        grid.addWidget(QLabel("解像度"), 1, 0)
        grid.addWidget(self._resolution, 1, 1)
        grid.addWidget(QLabel("品質"), 2, 0)
        grid.addLayout(quality_row, 2, 1)
        grid.setColumnStretch(1, 1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(grid)
        layout.addWidget(buttons)

    def export_settings(self) -> ExportSettings:
        """現在の選択内容。解像度 0 (元のサイズ) は None へ変換する"""
        side = int(self._resolution.currentData())
        return ExportSettings(
            codec=str(self._codec.currentData()),
            max_short_side=side or None,
            quality=self._quality.value(),
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
        self._settings.set_video_quality(result.quality)
        super().accept()
```

- [ ] **Step 4: テストが通ることを確認する**

Run: `python -m pytest tests/test_video_export_dialog.py -v`
Expected: PASS (全件)

- [ ] **Step 5: コミット**

```bash
git add mosaic_tool/video/export_dialog.py tests/test_video_export_dialog.py
git commit -m "feat(video): 書き出し設定ダイアログを追加する"
```

---

### Task 4: app.py — 書き出しフローへの組み込み

**Files:**
- Modify: `mosaic_tool/app.py` (`_export_video()`、`mosaic_tool/app.py:1100` 付近)
- Test: `tests/test_app.py` + 既存スイート全体 (`python -m pytest`)

**Interfaces:**
- Consumes:
  - `ExportDialog(info, settings, parent)` / `export_settings()` (Task 3)
  - `VideoExporter.__init__(..., strip_meta, export)` (Task 1)

- [ ] **Step 1: 失敗する配線テストを書く**

`tests/test_app.py` の動画系テストクラス群の並びに追加する
(既存の `video` fixture = 動画モードのウィンドウ、`QDialog` / `pytest` /
`video_ffmpeg` は import 済み):

```python
class TestExportDialogWiring:
    def test_cancel_does_not_start_export(self, video, monkeypatch):
        class FakeDialog:
            def __init__(self, *a, **k):
                pass

            def exec(self):
                return QDialog.DialogCode.Rejected

        monkeypatch.setattr("mosaic_tool.app.ExportDialog", FakeDialog)
        monkeypatch.setattr(
            "mosaic_tool.app.VideoExporter",
            lambda *a, **k: pytest.fail("キャンセルしたのに書き出しが始まった"),
        )
        video._export_video()
        assert video._exporter is None

    def test_accept_passes_the_chosen_settings_to_the_exporter(
        self, video, monkeypatch
    ):
        chosen = video_ffmpeg.ExportSettings("h265", 720, 40)

        class FakeDialog:
            def __init__(self, *a, **k):
                pass

            def exec(self):
                return QDialog.DialogCode.Accepted

            def export_settings(self):
                return chosen

        class FakeSignal:
            def connect(self, *a):
                pass

        created = []

        class FakeExporter:
            def __init__(self, *args):
                created.append(args)
                self.progress = FakeSignal()
                self.export_finished = FakeSignal()

            def start(self):
                pass

            def cancel(self):
                pass

        monkeypatch.setattr("mosaic_tool.app.ExportDialog", FakeDialog)
        monkeypatch.setattr("mosaic_tool.app.VideoExporter", FakeExporter)
        video._export_video()
        assert created and created[0][-1] == chosen
        # フェイクは完了シグナルを出さないため、後始末は手で戻す
        video._exporter = None
        video._export_dialog = None
```

Run: `python -m pytest tests/test_app.py::TestExportDialogWiring -v`
Expected: FAIL (`ImportError` / `AttributeError: ... 'ExportDialog'`)

- [ ] **Step 2: _export_video にダイアログを挟む**

`mosaic_tool/app.py` の import に追加 (既存の video 系 import の並びに合わせる):

```python
from mosaic_tool.video.export_dialog import ExportDialog
```

`QDialog` は `mosaic_tool/app.py` で import 済み。

`_export_video()` の先頭部分を変更する (Task 1 で暫定追加した
`video_ffmpeg.ExportSettings()` 引数はダイアログの選択値に差し替える):

```python
    def _export_video(self) -> None:
        """動画へモザイクを合成して書き出す(設定ダイアログ → 進捗ダイアログ)"""
        video = self._video
        if video is None or self._exporter is not None:
            return
        setting_dialog = ExportDialog(video.info, self._settings, self)
        if setting_dialog.exec() != QDialog.DialogCode.Accepted:
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
            setting_dialog.export_settings(),
        )
```

(以降の進捗ダイアログ・シグナル接続は変更しない)

- [ ] **Step 3: テストスイート全体が通ることを確認する**

Run: `python -m pytest`
Expected: PASS (全件。Step 1 の配線テスト 2 件を含む)

- [ ] **Step 4: アプリを起動して動作確認する**

Run: `just run <動画ファイル>` (手元に動画がなければユーザーへ依頼する)

確認項目:
- 保存操作でダイアログが出る (フォーマット・解像度・品質)
- 4K 未満の動画では元短辺以上のプリセットが出ない
- キャンセルで書き出しが始まらない
- OK で従来どおり進捗ダイアログ → 書き出し完了する
- 再度開くと前回の選択が初期値になっている

- [ ] **Step 5: コミット**

```bash
git add mosaic_tool/app.py tests/test_app.py
git commit -m "feat(video): 保存時に書き出し設定ダイアログを表示する"
```

---

## レビュー却下メモ

- settings.py → video/ffmpeg.py の import はレイヤ逆転 — 現時点で循環はなく、video/detect 側が settings を参照する予定もない。プリセット定数の重複定義を避ける方を優先し、循環が生じた時点で定数モジュール分離を検討する
- 品質スライダーに数値フィードバックがない — 設計段階で「両端ラベルのみ・数値の意味を出さない」で合意済み。CRF の丸めで無変化な区間があってもスライダー値自体は連続して保存されるため実害なし
