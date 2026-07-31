# 動画モードの操作性改善 実装計画

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 動画モードに「検出範囲の指定」「タイムラインの横スクロールと副目盛り」「再生モード」を加える。

**Architecture:** 下層から順に積む。(1) ffmpeg コマンド組み立て（GUI 非依存の純関数）、(2) 検出範囲ダイアログと再生エンジン（`video/player.py`）、(3) `TimelineBar` / `TimelineWindow` の UI 変更、(4) `canvas.py` の再生用描画パス、(5) `app.py` での配線。純ロジックは全て関数として切り出し、ffmpeg プロセスを起こさずにテストする。

**Tech Stack:** Python 3.11+ / PySide6 / Pillow / pytest（`QT_QPA_PLATFORM=offscreen`）/ ffmpeg（実行時に `runtime/ffmpeg` へ配置）

**Spec:** `docs/superpowers/specs/2026-07-30-video-mode-polish-design.md`

## Global Constraints

- コードのコメント・docstring・エラーメッセージ・UI 文言は日本語で書く
- テストは `python -m pytest tests/<file> -v` で実行する。テストファイル冒頭で
  `os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")` を PySide6 の import より前に置く
  （`from ... import` は `# noqa: E402` を付ける。既存テストと同じ書式）
- コミットメッセージは Conventional Commits 形式の日本語
- `Region` の同一性比較は `is` / `id()` を使う（`Region` は dataclass で `==` が構造比較になる）
- フレーム区間は両端含み（`start <= frame <= end`）
- ffmpeg / ffprobe は実行環境依存のため、テストではプロセスを起こさない
  （コマンド組み立ての検証と、`subprocess` / `QProcess` の monkeypatch のみ）
- 定数はモジュール先頭に名前付きで置く（ハードコードしない）

## File Structure

| ファイル | 役割 |
| --- | --- |
| `mosaic_tool/video/ffmpeg.py`（変更） | 範囲つきフレーム抽出コマンド、再生用ストリームコマンド、プロキシサイズ計算 |
| `mosaic_tool/video/detect_range_dialog.py`（新規） | 検出範囲ダイアログとタイムコード整形・件数計算の純関数 |
| `mosaic_tool/video/player.py`（新規） | 再生エンジン（ffmpeg 読み出しスレッド + 実時間ペーサ） |
| `mosaic_tool/video/timeline.py`（変更） | 下部バー: 検出間隔スピンを撤去し、再生ボタンと速度コンボを追加 |
| `mosaic_tool/video/timeline_window.py`（変更） | ホイールの分岐、副目盛りと縦線の描画、Space の中継 |
| `mosaic_tool/canvas.py`（変更） | 再生用のフレーム差し替え・範囲差し替え・操作停止 |
| `mosaic_tool/app.py`（変更） | 検出範囲ダイアログの呼び出し、範囲つき検出、再生の統合と排他 |
| `README.md`（変更） | 操作説明の更新 |

---

### Task 1: ffmpeg の範囲つきフレーム抽出コマンド

**Files:**
- Modify: `mosaic_tool/video/ffmpeg.py:173-187`
- Test: `tests/test_video_ffmpeg.py`

**Interfaces:**
- Produces:
  - `extract_frames_command(src: Path, info: VideoInfo, step: int, out_pattern: str, *, start: int = 0, count: int | None = None) -> list[str]`
    連番の k 枚目（1 始まり）は元動画のフレーム `start + (k - 1) * step` に対応する。

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_video_ffmpeg.py` の既存の抽出コマンドのテストが並ぶクラスへ追加する
（`info` フィクスチャは既存のものを使う）:

```python
    def test_extract_frames_seeks_to_the_start_frame(self, info):
        # 前フレームとの中間時刻へシークする(extract_frame_command と同じ方式)
        cmd = ffmpeg.extract_frames_command(
            Path("in.mp4"), info, 1, "out_%06d.jpg", start=60
        )
        assert cmd.index("-ss") < cmd.index("-i")
        assert float(cmd[cmd.index("-ss") + 1]) == pytest.approx(59.5 / info.fps)

    def test_extract_frames_limits_the_count(self, info):
        cmd = ffmpeg.extract_frames_command(
            Path("in.mp4"), info, 5, "out_%06d.jpg", start=10, count=7
        )
        assert cmd[cmd.index("-frames:v") + 1] == "7"

    def test_extract_frames_without_count_has_no_limit(self, info):
        cmd = ffmpeg.extract_frames_command(Path("in.mp4"), info, 1, "out_%06d.jpg")
        assert "-frames:v" not in cmd

    def test_extract_frames_from_the_head_seeks_to_zero(self, info):
        cmd = ffmpeg.extract_frames_command(Path("in.mp4"), info, 1, "out_%06d.jpg")
        assert float(cmd[cmd.index("-ss") + 1]) == 0.0
```

- [ ] **Step 2: テストが失敗することを確認する**

Run: `python -m pytest tests/test_video_ffmpeg.py -v`
Expected: FAIL（`extract_frames_command() got an unexpected keyword argument 'start'`）

- [ ] **Step 3: 最小の実装を書く**

`mosaic_tool/video/ffmpeg.py` の `extract_frames_command` を差し替える:

```python
def extract_frames_command(
    src: Path,
    info: VideoInfo,
    step: int,
    out_pattern: str,
    *,
    start: int = 0,
    count: int | None = None,
) -> list[str]:
    """検出用に step フレームおきの JPEG を out_pattern へ書き出すコマンド

    start から count 枚だけ取り出す。連番の k 枚目(1 始まり)は
    正規化後のフレーム start + (k-1) * step に対応する。
    -ss は extract_frame_command と同じく前フレームとの中間時刻を指し、
    丸め誤差で隣のフレームから始まらないようにする。
    """
    filters = _fps_filter(info)
    if step > 1:
        filters += f",select='not(mod(n\\,{step}))'"
    time = max(0.0, (start - 0.5) / info.fps)
    cmd = [
        str(ffmpeg_path()), "-v", "error",
        "-ss", f"{time:.6f}", "-i", str(src),
        "-vf", filters, "-fps_mode", "vfr",
    ]
    if count is not None:
        cmd += ["-frames:v", str(count)]
    cmd += ["-q:v", "2", out_pattern]
    return cmd
```

- [ ] **Step 4: テストが通ることを確認する**

Run: `python -m pytest tests/test_video_ffmpeg.py -v`
Expected: PASS（既存の `select` 系テストも通ること）

- [ ] **Step 5: コミット**

```bash
git add mosaic_tool/video/ffmpeg.py tests/test_video_ffmpeg.py
git commit -m "feat(video): 検出用フレーム抽出を開始フレームと枚数で絞れるようにする"
```

---

### Task 2: 再生用ストリームコマンドとプロキシサイズ

**Files:**
- Modify: `mosaic_tool/video/ffmpeg.py`（`decode_command` の直前へ追加）
- Test: `tests/test_video_ffmpeg.py`

**Interfaces:**
- Produces:
  - `PROXY_MAX_WIDTH: int = 960`
  - `proxy_size(info: VideoInfo, max_width: int = PROXY_MAX_WIDTH) -> tuple[int, int]`
  - `playback_command(src: Path, info: VideoInfo, start: int, size: tuple[int, int]) -> list[str]`
    stdout へ RGB24 の rawvideo を流し続ける。1 フレームは `w * h * 3` バイト固定。

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_video_ffmpeg.py` へ追加:

```python
class TestPlayback:
    def test_proxy_size_keeps_small_video_as_is(self):
        info = ffmpeg.VideoInfo(640, 480, 30.0, "30/1", 90, 3.0, None)
        assert ffmpeg.proxy_size(info) == (640, 480)

    def test_proxy_size_shrinks_to_the_max_width(self):
        info = ffmpeg.VideoInfo(1920, 1080, 30.0, "30/1", 90, 3.0, None)
        assert ffmpeg.proxy_size(info, 960) == (960, 540)

    def test_proxy_size_is_even(self):
        # 奇数サイズは 1px 切り詰める(プロキシは伸ばして表示するため影響しない)
        info = ffmpeg.VideoInfo(101, 57, 30.0, "30/1", 90, 3.0, None)
        assert ffmpeg.proxy_size(info, 960) == (100, 56)

    def test_playback_command_streams_rawvideo_from_the_start_frame(self):
        info = ffmpeg.VideoInfo(1920, 1080, 30.0, "30/1", 900, 30.0, None)
        cmd = ffmpeg.playback_command(Path("in.mp4"), info, 300, (960, 540))
        assert cmd.index("-ss") < cmd.index("-i")
        assert float(cmd[cmd.index("-ss") + 1]) == pytest.approx(299.5 / 30.0)
        assert "scale=960:540" in cmd[cmd.index("-vf") + 1]
        assert cmd[cmd.index("-f") + 1] == "rawvideo"
        assert cmd[cmd.index("-pix_fmt") + 1] == "rgb24"
        assert cmd[-1] == "-"
```

- [ ] **Step 2: テストが失敗することを確認する**

Run: `python -m pytest tests/test_video_ffmpeg.py::TestPlayback -v`
Expected: FAIL（`module 'mosaic_tool.video.ffmpeg' has no attribute 'proxy_size'`）

- [ ] **Step 3: 最小の実装を書く**

`mosaic_tool/video/ffmpeg.py` の定数部へ追加:

```python
# 再生プレビューの横幅上限 (px)。原寸の rawvideo はパイプの帯域が過大になる
PROXY_MAX_WIDTH = 960
```

`decode_command` の直前へ追加:

```python
def proxy_size(info: VideoInfo, max_width: int = PROXY_MAX_WIDTH) -> tuple[int, int]:
    """再生プレビューの描画サイズ。max_width を上限に縦横比を保った偶数サイズ

    偶数へ丸めるのは scale フィルタと相性を取るため。プロキシはシーン矩形へ
    伸ばして表示するので、1px の切り詰めは表示に影響しない。
    """
    if info.width <= max_width:
        width, height = info.width, info.height
    else:
        width = max_width
        height = max(1, round(info.height * max_width / info.width))
    return max(2, width - width % 2), max(2, height - height % 2)


def playback_command(
    src: Path, info: VideoInfo, start: int, size: tuple[int, int]
) -> list[str]:
    """再生用に start 以降を rawvideo (RGB24) として標準出力へ流し続けるコマンド

    1 フレームは幅 × 高さ × 3 バイト固定なので、読み出し側はフレーム境界を
    解析せずに切り出せる。
    """
    time = max(0.0, (start - 0.5) / info.fps)
    width, height = size
    return [
        str(ffmpeg_path()), "-v", "error",
        "-ss", f"{time:.6f}", "-i", str(src),
        "-vf", f"{_fps_filter(info)},scale={width}:{height}",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
    ]
```

- [ ] **Step 4: テストが通ることを確認する**

Run: `python -m pytest tests/test_video_ffmpeg.py -v`
Expected: PASS

- [ ] **Step 5: コミット**

```bash
git add mosaic_tool/video/ffmpeg.py tests/test_video_ffmpeg.py
git commit -m "feat(video): 再生プレビュー用の rawvideo ストリームコマンドを追加する"
```

---

### Task 3: 検出範囲ダイアログ

**Files:**
- Create: `mosaic_tool/video/detect_range_dialog.py`
- Test: `tests/test_video_detect_range.py`（新規）

**Interfaces:**
- Produces:
  - `DETECT_STEP_MAX: int = 120`（`video/timeline.py` から移設する値。Task 5 で旧定義を削除）
  - `format_timecode(frame: int, fps: float) -> str`
  - `detect_frame_count(start: int, end: int, step: int) -> int`
  - `DetectRangeDialog(total_frames: int, fps: float, current_frame: int, step: int, parent=None)`
    と `range_result() -> tuple[int, int, int]`（開始・終了・検出間隔）

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_video_detect_range.py`:

```python
"""検出範囲ダイアログ(タイムコード整形・件数計算・相互クランプ)の検証"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from mosaic_tool.video.detect_range_dialog import (  # noqa: E402
    DetectRangeDialog,
    detect_frame_count,
    format_timecode,
)


def make_dialog(total=1800, fps=30.0, current=120, step=1):
    QApplication.instance() or QApplication([])
    return DetectRangeDialog(total, fps, current, step)


class TestFormatTimecode:
    def test_zero_frame(self):
        assert format_timecode(0, 30.0) == "00:00.00"

    def test_minutes_and_seconds(self):
        assert format_timecode(1799, 30.0) == "00:59.97"

    def test_over_an_hour(self):
        assert format_timecode(108000, 30.0) == "1:00:00.00"

    def test_zero_fps_is_treated_as_the_head(self):
        # probe で fps が取れないケースは無いが、0 除算で落ちないことを守る
        assert format_timecode(100, 0.0) == "00:00.00"


class TestDetectFrameCount:
    def test_single_frame_range(self):
        assert detect_frame_count(10, 10, 1) == 1

    def test_step_counts_both_ends(self):
        assert detect_frame_count(0, 10, 5) == 3

    def test_step_larger_than_the_range(self):
        assert detect_frame_count(0, 3, 10) == 1

    def test_inverted_range_is_zero(self):
        assert detect_frame_count(10, 5, 1) == 0


class TestDialog:
    def test_defaults_to_current_frame_through_the_last_frame(self):
        dialog = make_dialog(total=1800, current=120)
        assert dialog.range_result() == (120, 1799, 1)

    def test_keeps_the_previous_step(self):
        assert make_dialog(step=7).range_result()[2] == 7

    def test_start_cannot_exceed_the_end(self):
        dialog = make_dialog(total=1800, current=0)
        dialog._end.setValue(100)
        dialog._start.setValue(500)
        assert dialog.range_result()[0] == 100

    def test_end_cannot_go_below_the_start(self):
        dialog = make_dialog(total=1800, current=200)
        dialog._end.setValue(10)
        assert dialog.range_result()[1] == 200

    def test_count_label_follows_the_values(self):
        dialog = make_dialog(total=1800, current=0)
        dialog._end.setValue(100)
        dialog._step.setValue(10)
        assert "11" in dialog._count_label.text()

    def test_time_labels_follow_the_values(self):
        dialog = make_dialog(total=1800, fps=30.0, current=0)
        dialog._start.setValue(30)
        assert dialog._start_time.text() == "00:01.00"
```

- [ ] **Step 2: テストが失敗することを確認する**

Run: `python -m pytest tests/test_video_detect_range.py -v`
Expected: FAIL（`ModuleNotFoundError: mosaic_tool.video.detect_range_dialog`）

- [ ] **Step 3: 最小の実装を書く**

`mosaic_tool/video/detect_range_dialog.py`:

```python
"""動画の自動検出の適用範囲(開始・終了フレーム)と検出間隔を決めるダイアログ"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QLabel,
    QSpinBox,
    QVBoxLayout,
)

# 検出間隔の上限 (フレーム)。これを超える間引きは漏れが大きく実用にならない
DETECT_STEP_MAX = 120

STEP_TOOLTIP = "自動検出を何フレームおきに行うか。増やすと速くなるが漏れやすくなる"


def format_timecode(frame: int, fps: float) -> str:
    """フレーム番号を MM:SS.ss (1 時間以上は H:MM:SS.ss) の表記にする"""
    seconds = frame / fps if fps > 0 else 0.0
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours >= 1:
        return f"{int(hours)}:{int(minutes):02d}:{secs:05.2f}"
    return f"{int(minutes):02d}:{secs:05.2f}"


def detect_frame_count(start: int, end: int, step: int) -> int:
    """範囲内で実際に検出するフレーム数(両端含み、step 間引き)"""
    if end < start:
        return 0
    return (end - start) // max(1, step) + 1


class DetectRangeDialog(QDialog):
    """検出の適用範囲を指定するモーダルダイアログ

    件数の表示が実行前の確認を兼ねる(従来の確認メッセージは出さない)。
    """

    def __init__(
        self,
        total_frames: int,
        fps: float,
        current_frame: int,
        step: int,
        parent=None,
    ):
        super().__init__(parent)
        self._fps = fps
        self.setWindowTitle("検出範囲")
        last = max(0, total_frames - 1)
        self._start = QSpinBox()
        self._start.setRange(0, last)
        self._start.setValue(min(max(0, current_frame), last))
        self._end = QSpinBox()
        self._end.setRange(0, last)
        self._end.setValue(last)
        self._step = QSpinBox()
        self._step.setRange(1, DETECT_STEP_MAX)
        self._step.setValue(step)
        self._step.setSuffix(" フレーム")
        self._step.setToolTip(STEP_TOOLTIP)
        self._start_time = QLabel()
        self._end_time = QLabel()
        self._count_label = QLabel()

        grid = QGridLayout()
        rows = (
            ("開始フレーム", self._start, self._start_time),
            ("終了フレーム", self._end, self._end_time),
        )
        for row, (text, spin, time_label) in enumerate(rows):
            grid.addWidget(QLabel(text), row, 0)
            grid.addWidget(spin, row, 1)
            grid.addWidget(time_label, row, 2)
        grid.addWidget(QLabel("検出間隔"), len(rows), 0)
        grid.addWidget(self._step, len(rows), 1)
        # 右端に余白を持たせ、ラベルが間延びしないようにする
        grid.setColumnStretch(3, 1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(grid)
        layout.addWidget(self._count_label)
        layout.addWidget(buttons)

        for spin in (self._start, self._end, self._step):
            spin.valueChanged.connect(self._on_value_changed)
        self._on_value_changed()

    def _on_value_changed(self) -> None:
        """開始 > 終了 にならないよう互いの範囲を狭め、表示を作り直す"""
        self._end.setMinimum(self._start.value())
        self._start.setMaximum(self._end.value())
        self._start_time.setText(format_timecode(self._start.value(), self._fps))
        self._end_time.setText(format_timecode(self._end.value(), self._fps))
        count = detect_frame_count(
            self._start.value(), self._end.value(), self._step.value()
        )
        self._count_label.setText(f"約 {count} フレームを検出します")

    def range_result(self) -> tuple[int, int, int]:
        """(開始フレーム, 終了フレーム, 検出間隔)"""
        return self._start.value(), self._end.value(), self._step.value()
```

- [ ] **Step 4: テストが通ることを確認する**

Run: `python -m pytest tests/test_video_detect_range.py -v`
Expected: PASS

- [ ] **Step 5: コミット**

```bash
git add mosaic_tool/video/detect_range_dialog.py tests/test_video_detect_range.py
git commit -m "feat(video): 検出範囲を指定するダイアログを追加する"
```

---

### Task 4: 範囲つき動画検出を app へ組み込む

**Files:**
- Modify: `mosaic_tool/app.py:64-74`（`_VideoDetectState`）、`mosaic_tool/app.py:931-1013`（検出の実行）、
  `mosaic_tool/app.py:131-138`（状態の初期化）、import 行
- Test: `tests/test_app.py`

**Interfaces:**
- Consumes: Task 1 の `extract_frames_command(..., start=, count=)`、Task 3 の
  `DetectRangeDialog` / `detect_frame_count`
- Produces:
  - `MainWindow._detect_step: int`（セッション中だけ保持する検出間隔。初期値 1）
  - `_VideoDetectState` に `start: int` と `end: int` を追加

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_app.py` の末尾へ追加する（`video` フィクスチャは既存のもの。
`VideoInfo(64, 48, 30.0, "30/1", 100, 3.3, None)` なので総フレーム 100）:

```python
class TestVideoDetectRange:
    @pytest.fixture
    def captured(self, video, monkeypatch):
        """検出範囲ダイアログを OK 固定にし、ffmpeg の起動引数を捕まえる"""
        calls = {}

        class FakeDialog:
            def __init__(self, total_frames, fps, current_frame, step, parent=None):
                calls["args"] = (total_frames, fps, current_frame, step)

            def exec(self):
                return QDialog.DialogCode.Accepted

            def range_result(self):
                return calls.get("result", (10, 39, 5))

        monkeypatch.setattr("mosaic_tool.app.DetectRangeDialog", FakeDialog)
        monkeypatch.setattr(
            "mosaic_tool.app.QProcess.start",
            lambda self, program, args: calls.setdefault("cmd", [program, *args]),
        )
        return calls

    def test_dialog_receives_the_current_frame_and_video_info(self, video, captured):
        video._timeline.seek(40)
        video._start_video_detect({"m.pt": {"conf": 0.5, "classes": []}})
        assert captured["args"] == (100, 30.0, 40, 1)
        video._cleanup_video_detect()

    def test_extraction_uses_the_selected_range(self, video, captured):
        video._start_video_detect({"m.pt": {"conf": 0.5, "classes": []}})
        cmd = captured["cmd"]
        # 開始 10 / 終了 39 / 間隔 5 なので 6 枚
        assert cmd[cmd.index("-frames:v") + 1] == "6"
        assert float(cmd[cmd.index("-ss") + 1]) == pytest.approx(9.5 / 30.0)
        assert video._video_detect.start == 10
        assert video._video_detect.end == 39
        video._cleanup_video_detect()

    def test_cancelling_the_dialog_does_not_extract(self, video, monkeypatch):
        class FakeDialog:
            def __init__(self, *a, **k):
                pass

            def exec(self):
                return QDialog.DialogCode.Rejected

        monkeypatch.setattr("mosaic_tool.app.DetectRangeDialog", FakeDialog)
        monkeypatch.setattr(
            "mosaic_tool.app.QProcess.start",
            lambda *a: pytest.fail("展開が始まった"),
        )
        video._start_video_detect({"m.pt": {"conf": 0.5, "classes": []}})
        assert video._video_detect is None

    def test_the_step_is_kept_for_the_next_run(self, video, captured):
        video._start_video_detect({"m.pt": {"conf": 0.5, "classes": []}})
        video._cleanup_video_detect()
        assert video._detect_step == 5

    def test_detected_frames_are_offset_by_the_start(self, video, captured):
        video._start_video_detect({"m.pt": {"conf": 0.5, "classes": []}})
        state = video._video_detect
        state.files = [Path(f"f{i}.jpg") for i in range(6)]
        state.idx = 2
        video._on_video_frame_detected(
            [{"bbox": [0, 0, 10, 10]}]
        )
        # 開始 10 + 2 枚目 × 間隔 5 = フレーム 20
        assert video._video.regions[0].start == 20
        video._cleanup_video_detect()

    def test_intervals_are_clamped_to_the_range_end(self, video, captured):
        captured["result"] = (10, 22, 5)
        video._start_video_detect({"m.pt": {"conf": 0.5, "classes": []}})
        state = video._video_detect
        state.files = [Path(f"f{i}.jpg") for i in range(3)]
        state.idx = 2  # フレーム 20。間隔 5 なら本来 24 まで伸びる
        video._on_video_frame_detected([{"bbox": [0, 0, 10, 10]}])
        assert video._video.regions[0].end == 22
        video._cleanup_video_detect()
```

`tests/test_app.py` の import に `from pathlib import Path` が無ければ追加する（既存にあれば不要）。

- [ ] **Step 2: テストが失敗することを確認する**

Run: `python -m pytest tests/test_app.py::TestVideoDetectRange -v`
Expected: FAIL（`mosaic_tool.app` に `DetectRangeDialog` が無い）

- [ ] **Step 3: 最小の実装を書く**

`mosaic_tool/app.py` の import へ追加:

```python
from mosaic_tool.video.detect_range_dialog import DetectRangeDialog, detect_frame_count
```

`_VideoDetectState` へフィールドを追加（`step` の下）:

```python
    start: int = 0   # 検出範囲の開始フレーム
    end: int = 0     # 同終了フレーム(区間末尾のクランプに使う)
```

`__init__` の動画モードの状態の並びへ追加:

```python
        # 検出範囲ダイアログの検出間隔(セッション中だけ引き継ぐ)
        self._detect_step = 1
```

`_start_video_detect` を差し替える:

```python
    def _start_video_detect(self, models: dict) -> None:
        """指定範囲のフレームを取り出し、順に検出して区間つき範囲を作る"""
        video = self._video
        if video is None or self._worker.is_busy() or self._reject_while_video_busy():
            return
        dialog = DetectRangeDialog(
            video.info.frame_count, video.info.fps, video.frame, self._detect_step, self
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        start, end, step = dialog.range_result()
        self._detect_step = step
        count = detect_frame_count(start, end, step)
        if count <= 0:
            return
        tmp = Path(tempfile.mkdtemp(prefix="mosaic_vdetect_"))
        proc = QProcess(self)
        proc.finished.connect(self._on_frames_extracted)
        self._video_detect = _VideoDetectState(
            models=models, step=step, dir=tmp, proc=proc, start=start, end=end
        )
        if self._detect_window is not None:
            self._detect_window.set_running(True)
        self.statusBar().showMessage("フレームを展開中...")
        cmd = video_ffmpeg.extract_frames_command(
            video.path,
            video.info,
            step,
            str(tmp / "frame_%06d.jpg"),
            start=start,
            count=count,
        )
        proc.start(cmd[0], cmd[1:])
```

`_on_video_frame_detected` のフレーム番号とクランプを直す:

```python
        frame = state.start + state.idx * state.step
```

```python
        intervals = merge_detections(
            state.dets,
            step=state.step,
            # 区間の末尾伸長が検出範囲の外へ出ないようクランプする
            total_frames=state.end + 1,
        )
```

- [ ] **Step 4: テストが通ることを確認する**

Run: `python -m pytest tests/test_app.py -v`
Expected: PASS（`QMessageBox.question` を前提にしていた既存の動画検出テストがあれば、
ダイアログ前提へ書き換える）

- [ ] **Step 5: コミット**

```bash
git add mosaic_tool/app.py tests/test_app.py
git commit -m "feat(video): 自動検出の適用範囲をダイアログで指定できるようにする"
```

---

### Task 5: 下部バーの検出間隔を撤去し再生操作を置く

**Files:**
- Modify: `mosaic_tool/video/timeline.py`
- Modify: `mosaic_tool/app.py`（`_timeline.detect_step()` の呼び出しが残っていれば削除）
- Test: `tests/test_video_timeline.py`

**Interfaces:**
- Produces:
  - `TimelineBar.play_clicked: Signal()`（▶/⏸ の押下。再生するかどうかは `app.py` が決める）
  - `TimelineBar.set_playing(playing: bool) -> None`（ボタンの表示を切り替える）
  - `TimelineBar.speed() -> float`（0.25 / 0.5 / 1.0 / 2.0）
  - `TimelineBar.speed_changed: Signal(float)`
  - `TimelineBar.detect_step()` と `DETECT_STEP_MAX` を削除（検出間隔は Task 3 のダイアログへ移設済み）

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_video_timeline.py` へ追加:

```python
class TestPlaybackControls:
    def test_detect_step_is_gone(self):
        bar = make_bar()
        assert not hasattr(bar, "detect_step")

    def test_play_button_emits_the_request(self):
        bar = make_bar()
        fired = []
        bar.play_clicked.connect(lambda: fired.append(True))
        bar._play_btn.click()
        assert fired == [True]

    def test_play_button_text_follows_the_state(self):
        from mosaic_tool.video.timeline import PAUSE_TEXT, PLAY_TEXT

        bar = make_bar()
        assert bar._play_btn.text() == PLAY_TEXT
        bar.set_playing(True)
        assert bar._play_btn.text() == PAUSE_TEXT
        bar.set_playing(False)
        assert bar._play_btn.text() == PLAY_TEXT

    def test_play_button_does_not_take_focus(self):
        # Space のショートカットとボタンの押下が二重に効かないようにする
        from PySide6.QtCore import Qt

        bar = make_bar()
        assert bar._play_btn.focusPolicy() == Qt.FocusPolicy.NoFocus

    def test_speed_defaults_to_normal(self):
        assert make_bar().speed() == 1.0

    def test_speed_selection_emits_and_is_readable(self):
        bar = make_bar()
        fired = []
        bar.speed_changed.connect(fired.append)
        bar._speed_combo.setCurrentIndex(0)
        assert bar.speed() == 0.25
        assert fired == [0.25]
```

- [ ] **Step 2: テストが失敗することを確認する**

Run: `python -m pytest tests/test_video_timeline.py -v`
Expected: FAIL（`TimelineBar` に `play_clicked` が無い）

- [ ] **Step 3: 最小の実装を書く**

`mosaic_tool/video/timeline.py` を差し替える（`DETECT_STEP_MAX` と検出間隔スピンを削除し、
再生ボタンと速度コンボを追加する）:

```python
"""動画モードのタイムライン UI(シーク・再生操作)

区間の表示と編集はタイムラインウィンドウ(video/timeline_window.py)が担い、
検出の条件は検出範囲ダイアログ(video/detect_range_dialog.py)が持つ。
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QWidget,
)

PLAY_TEXT = "▶"
PAUSE_TEXT = "⏸"

# 再生速度の選択肢(倍率)。既定は 1.0
SPEEDS = (0.25, 0.5, 1.0, 2.0)
DEFAULT_SPEED = 1.0


class TimelineBar(QWidget):
    """キャンバス下に出すタイムライン。動画モードのときだけ表示する"""

    frame_changed = Signal(int)   # シークやコマ送りでフレームが変わった
    play_clicked = Signal()       # ▶ / ⏸ が押された(実際の開始・停止は app 側)
    speed_changed = Signal(float)  # 再生速度が変わった

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        self._prev_btn = QPushButton("◀")
        self._prev_btn.setFixedWidth(28)
        self._prev_btn.clicked.connect(lambda: self.step(-1))
        layout.addWidget(self._prev_btn)
        # 再生ボタンは Space のショートカットと二重に効かないようフォーカスを持たせない
        self._play_btn = QPushButton(PLAY_TEXT)
        self._play_btn.setFixedWidth(28)
        self._play_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._play_btn.clicked.connect(self.play_clicked)
        layout.addWidget(self._play_btn)
        self._slider = QSlider(Qt.Orientation.Horizontal)
        self._slider.setRange(0, 0)
        self._slider.valueChanged.connect(self.frame_changed)
        self._slider.valueChanged.connect(lambda _: self._update_label())
        layout.addWidget(self._slider, 1)
        self._next_btn = QPushButton("▶|")
        self._next_btn.setFixedWidth(28)
        self._next_btn.clicked.connect(lambda: self.step(1))
        layout.addWidget(self._next_btn)
        self._frame_label = QLabel(" 0 / 0 ")
        layout.addWidget(self._frame_label)
        layout.addWidget(QLabel(" 速度 "))
        self._speed_combo = QComboBox()
        for speed in SPEEDS:
            self._speed_combo.addItem(f"{speed}x", speed)
        self._speed_combo.setCurrentIndex(SPEEDS.index(DEFAULT_SPEED))
        self._speed_combo.currentIndexChanged.connect(
            lambda _: self.speed_changed.emit(self.speed())
        )
        layout.addWidget(self._speed_combo)

    def set_range(self, total_frames: int) -> None:
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
        self._play_btn.setText(PAUSE_TEXT if playing else PLAY_TEXT)

    def speed(self) -> float:
        return float(self._speed_combo.currentData())

    def _update_label(self) -> None:
        self._frame_label.setText(
            f" {self._slider.value()} / {self._slider.maximum()} "
        )
```

コマ送りの次へボタンは `▶` が再生ボタンと紛らわしいため `▶|` へ変える。
`tests/test_video_timeline.py` の既存テストが検出間隔（`detect_step`）を見ているものは削除する。

`mosaic_tool/app.py` に `self._timeline.detect_step()` の呼び出しが残っていれば削除する
（Task 4 で `_start_video_detect` を差し替えているので通常は残らない）。

- [ ] **Step 4: テストが通ることを確認する**

Run: `python -m pytest tests/test_video_timeline.py tests/test_app.py -v`
Expected: PASS

- [ ] **Step 5: コミット**

```bash
git add mosaic_tool/video/timeline.py tests/test_video_timeline.py mosaic_tool/app.py
git commit -m "feat(video): 下部バーの検出間隔を再生操作へ置き換える"
```

---

### Task 6: タイムラインのホイールを横スクロールにする

**Files:**
- Modify: `mosaic_tool/video/timeline_window.py:62-66`（シグナル定義）、
  `mosaic_tool/video/timeline_window.py:249-264`（`wheelEvent`）、
  `mosaic_tool/video/timeline_window.py:373-383`（`TimelineWindow` の配線）
- Test: `tests/test_video_timeline_window.py`

**Interfaces:**
- Produces:
  - `TimelineArea.hscroll_requested: Signal(int)` — 水平スクロールバーへ加算する px 量
    （正で右へ進む）。ズームのアンカー補正に使う既存の `scroll_requested`（絶対値）とは別物

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_video_timeline_window.py` へ追加（既存のヘルパーがあればそれを使い、
無ければ以下の `make_area` を定義する）:

```python
class TestWheel:
    def _wheel(self, area, modifiers, dy=120):
        from PySide6.QtCore import QPoint, QPointF
        from PySide6.QtGui import QWheelEvent

        event = QWheelEvent(
            QPointF(200.0, 40.0),
            QPointF(200.0, 40.0),
            QPoint(0, 0),
            QPoint(0, dy),
            Qt.MouseButton.NoButton,
            modifiers,
            Qt.ScrollPhase.NoScrollPhase,
            False,
        )
        area.wheelEvent(event)

    def test_plain_wheel_scrolls_horizontally(self):
        area = make_area()
        fired = []
        area.hscroll_requested.connect(fired.append)
        self._wheel(area, Qt.KeyboardModifier.NoModifier, dy=120)
        # ホイール上回転で左へ(加算量は負)
        assert fired == [-120]

    def test_wheel_down_scrolls_right(self):
        area = make_area()
        fired = []
        area.hscroll_requested.connect(fired.append)
        self._wheel(area, Qt.KeyboardModifier.NoModifier, dy=-120)
        assert fired == [120]

    def test_ctrl_wheel_still_zooms(self):
        area = make_area()
        fired = []
        area.hscroll_requested.connect(fired.append)
        before = area._px_per_frame
        self._wheel(area, Qt.KeyboardModifier.ControlModifier, dy=120)
        assert area._px_per_frame > before
        assert fired == []

    def test_shift_wheel_does_not_scroll_horizontally(self):
        area = make_area()
        fired = []
        area.hscroll_requested.connect(fired.append)
        self._wheel(area, Qt.KeyboardModifier.ShiftModifier, dy=120)
        assert fired == []

    def test_window_applies_the_relative_scroll(self):
        window = TimelineWindow()
        window.set_total(10000)
        bar = window._scroll.horizontalScrollBar()
        bar.setValue(300)
        window._area.hscroll_requested.emit(120)
        assert bar.value() == 420
```

`make_area` が未定義なら、テストファイル冒頭のヘルパーとして定義する:

```python
def make_area(total=10000):
    QApplication.instance() or QApplication([])
    area = TimelineArea()
    area.set_total(total)
    return area
```

- [ ] **Step 2: テストが失敗することを確認する**

Run: `python -m pytest tests/test_video_timeline_window.py::TestWheel -v`
Expected: FAIL（`TimelineArea` に `hscroll_requested` が無い）

- [ ] **Step 3: 最小の実装を書く**

`TimelineArea` のシグナル定義へ追加:

```python
    hscroll_requested = Signal(int)  # ホイールによる横スクロール量(相対 px)
```

`wheelEvent` を差し替える:

```python
    def wheelEvent(self, event) -> None:
        """Ctrl+ホイールで横ズーム、修飾なしで横スクロール、Shift で縦スクロール"""
        modifiers = event.modifiers()
        if modifiers & Qt.KeyboardModifier.ShiftModifier:
            super().wheelEvent(event)
            return
        if modifiers & Qt.KeyboardModifier.ControlModifier:
            notches = event.angleDelta().y() / 120.0
            if notches == 0:
                super().wheelEvent(event)
                return
            cursor_x = event.position().x()
            frame = self._frame_at(cursor_x)
            # ズーム前のカーソル位置(ビューポート座標)を保ったままスクロールし直す
            viewport_x = cursor_x - self._scroll_x
            self._zoom(ZOOM_STEP**notches)
            self.scroll_requested.emit(int(self._x(frame) - viewport_x))
            event.accept()
            return
        # 横向きのホイールを持つデバイスでは x 側だけが動く
        delta = event.angleDelta().y() or event.angleDelta().x()
        if delta == 0:
            super().wheelEvent(event)
            return
        # 上回転(正)で左へ進めるため符号を反転する
        self.hscroll_requested.emit(-delta)
        event.accept()
```

`TimelineWindow.__init__` の配線へ追加（`scroll_requested` の接続の隣）:

```python
        self._area.hscroll_requested.connect(self._scroll_by)
```

`_scroll_to` の隣へ追加:

```python
    def _scroll_by(self, delta: int) -> None:
        """ホイールによる横スクロール(現在位置からの相対移動)"""
        bar = self._scroll.horizontalScrollBar()
        bar.setValue(bar.value() + delta)
```

- [ ] **Step 4: テストが通ることを確認する**

Run: `python -m pytest tests/test_video_timeline_window.py -v`
Expected: PASS

- [ ] **Step 5: コミット**

```bash
git add mosaic_tool/video/timeline_window.py tests/test_video_timeline_window.py
git commit -m "feat(video): タイムラインのホイールを横スクロールに割り当てる"
```

---

### Task 7: タイムラインに副目盛りと縦線を描く

**Files:**
- Modify: `mosaic_tool/video/timeline_window.py`（定数、`_minor_interval`、`paintEvent`、
  `_paint_ruler`、`_paint_rows` の分割）
- Test: `tests/test_video_timeline_window.py`

**Interfaces:**
- Produces:
  - `MIN_MINOR_PX: int = 8`
  - `_minor_interval(major: int, px_per_frame: float) -> int` — 分割できなければ `major` を返す
  - `TimelineArea._paint_grid(painter, rect)` — 行エリアの縦線（主目盛りは明るく、副目盛りは暗く）

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_video_timeline_window.py` へ追加:

```python
class TestMinorInterval:
    def test_divides_by_ten_when_there_is_room(self):
        from mosaic_tool.video.timeline_window import _minor_interval

        # 主目盛り 100 フレーム、1 フレーム 2px なら 1/10 (10 フレーム = 20px) が入る
        assert _minor_interval(100, 2.0) == 10

    def test_falls_back_to_a_coarser_division(self):
        from mosaic_tool.video.timeline_window import _minor_interval

        # 1/10 では 8px を割るので 1/5 (20 フレーム = 10px) を選ぶ
        assert _minor_interval(100, 0.5) == 20

    def test_returns_the_major_interval_when_it_cannot_divide(self):
        from mosaic_tool.video.timeline_window import _minor_interval

        assert _minor_interval(1, 20.0) == 1

    def test_returns_the_major_interval_when_there_is_no_room(self):
        from mosaic_tool.video.timeline_window import _minor_interval

        assert _minor_interval(100, 0.05) == 100


class TestGridRendering:
    def test_paints_without_error(self):
        # 縦線を含む描画一式が例外なく通ることを確認する(見た目は目視で確認する)
        from PySide6.QtGui import QPixmap

        area = make_area()
        area.set_data([], None)
        area.resize(400, 120)
        pixmap = QPixmap(400, 120)
        area.render(pixmap)

    def test_grid_is_drawn_above_the_row_background(self):
        # 行が無いときは縦線を描かない(下端が RULER_H と同じになる)
        from mosaic_tool.video.timeline_window import RULER_H

        area = make_area()
        area.set_data([], None)
        assert area._row_top(0) == RULER_H + 2  # ROW_GAP
```

- [ ] **Step 2: テストが失敗することを確認する**

Run: `python -m pytest tests/test_video_timeline_window.py::TestMinorInterval -v`
Expected: FAIL（`cannot import name '_minor_interval'`）

- [ ] **Step 3: 最小の実装を書く**

`mosaic_tool/video/timeline_window.py` の定数部へ追加:

```python
# 副目盛りの最小間隔 (px)。これより詰まる分割は使わない
MIN_MINOR_PX = 8
# 副目盛りの分割数(細かい順に試す)
_MINOR_DIVISORS = (10, 5, 2)
```

配色の定義へ追加:

```python
_GRID_MAJOR = QColor(0x3C, 0x3C, 0x3C)  # 主目盛りの縦線
_GRID_MINOR = QColor(0x33, 0x33, 0x33)  # 副目盛りの縦線
```

`_tick_interval` の下へ追加:

```python
def _minor_interval(major: int, px_per_frame: float) -> int:
    """主目盛りを分割した副目盛りの幅

    割り切れて、かつ間隔が MIN_MINOR_PX 以上になる最も細かい分割を選ぶ。
    分割できなければ major をそのまま返す(この場合は副目盛りを描かない)。
    """
    for divisor in _MINOR_DIVISORS:
        if major % divisor:
            continue
        minor = major // divisor
        if minor * px_per_frame >= MIN_MINOR_PX:
            return minor
    return major
```

`paintEvent` を差し替え、行背景・縦線・バーを別メソッドへ分ける:

```python
    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        rect = QRectF(event.rect())
        painter.fillRect(rect, _BG)
        self._paint_rows(painter, rect)
        self._paint_grid(painter, rect)
        self._paint_bars(painter, rect)
        self._paint_ruler(painter, rect)
        self._paint_playhead(painter)
        self._paint_labels(painter)
        painter.end()
```

`_paint_ruler` を差し替える（主目盛りは長い線とフレーム番号、副目盛りは短い線）:

```python
    RULER_MAJOR_TICK = 5  # 主目盛りの線の長さ (px)
    RULER_MINOR_TICK = 3  # 副目盛りの線の長さ (px)

    def _paint_ruler(self, painter: QPainter, rect: QRectF) -> None:
        """上端のルーラーに主副の目盛りとフレーム番号を描く"""
        painter.fillRect(QRectF(rect.left(), 0, rect.width(), RULER_H), _RULER_BG)
        major = _tick_interval(self._px_per_frame)
        minor = _minor_interval(major, self._px_per_frame)
        first = max(0, self._frame_at(rect.left()) // minor * minor)
        last = self._frame_at(rect.right())
        painter.setPen(_TICK_COLOR)
        for frame in range(first, last + 1, minor):
            x = int(self._x(frame))
            is_major = frame % major == 0
            length = self.RULER_MAJOR_TICK if is_major else self.RULER_MINOR_TICK
            painter.drawLine(x, RULER_H - length, x, RULER_H)
            if is_major:
                painter.drawText(x + 2, RULER_H - 7, str(frame))
```

既存の `_paint_rows` を、行背景だけを描く版とバーだけを描く版に分ける:

```python
    def _paint_rows(self, painter: QPainter, rect: QRectF) -> None:
        """各行の背景を描く"""
        for i in range(len(self._rows)):
            painter.fillRect(
                QRectF(rect.left(), self._row_top(i), rect.width(), ROW_H), _ROW_BG
            )

    def _paint_grid(self, painter: QPainter, rect: QRectF) -> None:
        """行エリアに縦線を引く(主目盛りは明るく、副目盛りは暗く)

        行背景の後・区間バーの前に描き、バーが線に埋もれないようにする。
        """
        bottom = self._row_top(len(self._rows))
        if bottom <= RULER_H:
            return
        major = _tick_interval(self._px_per_frame)
        minor = _minor_interval(major, self._px_per_frame)
        first = max(0, self._frame_at(rect.left()) // minor * minor)
        last = self._frame_at(rect.right())
        for frame in range(first, last + 1, minor):
            painter.setPen(_GRID_MAJOR if frame % major == 0 else _GRID_MINOR)
            x = int(self._x(frame))
            painter.drawLine(x, int(RULER_H), x, int(bottom))

    def _paint_bars(self, painter: QPainter, rect: QRectF) -> None:
        """可視フレーム範囲に掛かる区間バーを描く

        自動検出はフレームごとの区間で数千個になり得るため、
        描画対象を可視範囲に掛かるバーだけへ絞る。
        """
        left_frame = self._frame_at_raw(rect.left()) - 1
        right_frame = self._frame_at_raw(rect.right()) + 1
        painter.setPen(Qt.PenStyle.NoPen)
        for i, row in enumerate(self._rows):
            for vr in row.items:
                if vr.end < left_frame or vr.start > right_frame:
                    continue
                selected = self._selected is not None and vr.region is self._selected
                bar = self._bar_rect(i, vr)
                painter.setBrush(_SELECTED_COLOR if selected else _BAR_COLOR)
                painter.drawRect(bar)
                if selected:
                    # 端ハンドル(白い縦線)でドラッグできることを示す
                    painter.setBrush(_HANDLE_COLOR)
                    for x in (bar.left(), bar.right() - 3):
                        painter.drawRect(QRectF(x, bar.top(), 3, bar.height()))
```

- [ ] **Step 4: テストが通ることを確認する**

Run: `python -m pytest tests/test_video_timeline_window.py -v`
Expected: PASS

- [ ] **Step 5: コミット**

```bash
git add mosaic_tool/video/timeline_window.py tests/test_video_timeline_window.py
git commit -m "feat(video): タイムラインに副目盛りと縦線を描く"
```

---

### Task 8: 再生エンジン

**Files:**
- Create: `mosaic_tool/video/player.py`
- Test: `tests/test_video_player.py`（新規）

**Interfaces:**
- Consumes: Task 2 の `proxy_size` / `playback_command`
- Produces:
  - `QUEUE_SIZE: int = 30` / `TICK_MS: int = 10`
  - `target_frame(start: int, elapsed_ms: int, fps: float, speed: float, last: int) -> int`
  - `split_frames(buffer: bytearray, frame_bytes: int) -> list[bytes]`
    （完成したフレームを取り出し、`buffer` からはその分を削る）
  - `FrameReader(QThread)`: `queue: queue.Queue`、`stop()`、`failed: Signal(str)`
  - `VideoPlayer(QObject)`: `frame_ready: Signal(int, QImage)`、`finished: Signal()`、
    `failed: Signal(str)`、`start(frame: int)`、`stop()`、`is_playing() -> bool`、
    `set_speed(speed: float)`

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_video_player.py`:

```python
"""再生エンジン(目標フレームの算出・rawvideo の切り出し・終端の扱い)の検証"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from mosaic_tool.video.ffmpeg import VideoInfo  # noqa: E402
from mosaic_tool.video.player import (  # noqa: E402
    VideoPlayer,
    split_frames,
    target_frame,
)


def make_info(frame_count=100):
    return VideoInfo(64, 48, 30.0, "30/1", frame_count, frame_count / 30.0, None)


class TestTargetFrame:
    def test_start_of_playback_stays_on_the_start_frame(self):
        assert target_frame(10, 0, 30.0, 1.0, 99) == 10

    def test_advances_with_the_frame_rate(self):
        assert target_frame(0, 1000, 30.0, 1.0, 99) == 30

    def test_speed_scales_the_advance(self):
        assert target_frame(0, 1000, 30.0, 2.0, 99) == 60
        assert target_frame(0, 1000, 30.0, 0.5, 99) == 15

    def test_clamped_to_the_last_frame(self):
        assert target_frame(0, 10_000, 30.0, 1.0, 99) == 99


class TestSplitFrames:
    def test_takes_complete_frames_and_keeps_the_rest(self):
        buffer = bytearray(b"aaabbbc")
        frames = split_frames(buffer, 3)
        assert frames == [b"aaa", b"bbb"]
        assert bytes(buffer) == b"c"

    def test_no_complete_frame(self):
        buffer = bytearray(b"ab")
        assert split_frames(buffer, 3) == []
        assert bytes(buffer) == b"ab"


class TestVideoPlayer:
    def test_starting_at_the_last_frame_finishes_immediately(self, tmp_path):
        QApplication.instance() or QApplication([])
        player = VideoPlayer(tmp_path / "movie.mp4", make_info(100))
        fired = []
        player.finished.connect(lambda: fired.append(True))
        player.start(99)
        assert fired == [True]
        assert not player.is_playing()

    def test_speed_is_applied_without_playing(self, tmp_path):
        QApplication.instance() or QApplication([])
        player = VideoPlayer(tmp_path / "movie.mp4", make_info(100))
        player.set_speed(2.0)
        assert player._speed == 2.0
```

- [ ] **Step 2: テストが失敗することを確認する**

Run: `python -m pytest tests/test_video_player.py -v`
Expected: FAIL（`ModuleNotFoundError: mosaic_tool.video.player`）

- [ ] **Step 3: 最小の実装を書く**

`mosaic_tool/video/player.py`:

```python
"""動画モードの再生: ffmpeg から連続でフレームを受け取り実時間で表示する

1 フレームごとに ffmpeg を起動する方式では数 fps しか出ないため、再生開始位置から
長寿命のプロセスを 1 本だけ立て、プロキシ解像度の rawvideo をパイプで受け取る。
表示は壁時計に合わせ、遅れたフレームは捨てる(実時間優先)。
"""
from __future__ import annotations

import queue
import subprocess
from pathlib import Path

from PySide6.QtCore import QElapsedTimer, QObject, QThread, QTimer, Signal
from PySide6.QtGui import QImage

from mosaic_tool.video import ffmpeg as video_ffmpeg
from mosaic_tool.video.ffmpeg import VideoInfo

# 先読みするフレーム数。満杯になれば読み出しが止まり ffmpeg の流量も自然に絞られる
QUEUE_SIZE = 30
# 目標フレームを見に行く間隔 (ms)
TICK_MS = 10
# パイプから 1 回に読むバイト数
READ_CHUNK = 1 << 16
# キューへの投入待ちの区切り (秒)。停止要求を取りこぼさないために区切って待つ
PUT_TIMEOUT = 0.1
# 停止時にスレッドの終了を待つ上限 (ms)
STOP_WAIT_MS = 2000


def target_frame(
    start: int, elapsed_ms: int, fps: float, speed: float, last: int
) -> int:
    """再生開始から elapsed_ms 経った時点で表示すべきフレーム(末尾でクランプ)"""
    frame = start + int(elapsed_ms / 1000.0 * fps * speed)
    return min(frame, last)


def split_frames(buffer: bytearray, frame_bytes: int) -> list[bytes]:
    """受信バッファから完成したフレームを取り出す(取り出した分は buffer から削る)"""
    frames = []
    while len(buffer) >= frame_bytes:
        frames.append(bytes(buffer[:frame_bytes]))
        del buffer[:frame_bytes]
    return frames


class FrameReader(QThread):
    """ffmpeg を 1 本だけ起動し、rawvideo フレームをキューへ流し込むスレッド"""

    failed = Signal(str)

    def __init__(
        self,
        path: Path,
        info: VideoInfo,
        start: int,
        size: tuple[int, int],
        parent=None,
    ):
        super().__init__(parent)
        self._path = path
        self._info = info
        self._start = start
        self._size = size
        self.queue: queue.Queue = queue.Queue(maxsize=QUEUE_SIZE)
        self._stop = False
        self._proc: subprocess.Popen | None = None

    def stop(self) -> None:
        """読み出しを打ち切る(キューを空にして投入待ちも解く)"""
        self._stop = True
        proc = self._proc
        if proc is not None and proc.poll() is None:
            proc.kill()
        self._drain()

    def _drain(self) -> None:
        while True:
            try:
                self.queue.get_nowait()
            except queue.Empty:
                return

    def run(self) -> None:
        width, height = self._size
        frame_bytes = width * height * 3
        cmd = video_ffmpeg.playback_command(
            self._path, self._info, self._start, self._size
        )
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                creationflags=video_ffmpeg.subprocess_flags(),
            )
        except OSError as e:
            self.failed.emit(f"再生を開始できません: {e}")
            return
        self._proc = proc
        buffer = bytearray()
        while not self._stop:
            chunk = proc.stdout.read(READ_CHUNK)
            if not chunk:
                break
            buffer.extend(chunk)
            for data in split_frames(buffer, frame_bytes):
                self._put(data)
                if self._stop:
                    break
        proc.stdout.close()
        if proc.poll() is None:
            proc.kill()
        proc.wait()

    def _put(self, data: bytes) -> None:
        """キューへ入れる(満杯なら停止要求を見ながら待つ)"""
        while not self._stop:
            try:
                self.queue.put(data, timeout=PUT_TIMEOUT)
                return
            except queue.Full:
                continue


class VideoPlayer(QObject):
    """再生の司令塔。壁時計に合わせてキューからフレームを取り出す"""

    frame_ready = Signal(int, QImage)  # (フレーム番号, プロキシ解像度の画像)
    finished = Signal()
    failed = Signal(str)

    def __init__(self, path: Path, info: VideoInfo, parent=None):
        super().__init__(parent)
        self._path = path
        self._info = info
        self._size = video_ffmpeg.proxy_size(info)
        self._speed = 1.0
        self._start = 0   # 時計の基準フレーム
        self._index = 0   # 直前に表示したフレーム
        self._next = 0    # 次にキューから出てくるフレーム番号
        self._reader: FrameReader | None = None
        self._clock = QElapsedTimer()
        self._timer = QTimer(self)
        self._timer.setInterval(TICK_MS)
        self._timer.timeout.connect(self._on_tick)

    def is_playing(self) -> bool:
        return self._reader is not None

    def set_speed(self, speed: float) -> None:
        """再生速度を変える(再生中は現在位置から時計を張り直す)"""
        self._speed = speed
        if self.is_playing():
            self._start = self._index
            self._clock.restart()

    def start(self, frame: int) -> None:
        """frame から再生を始める。末尾なら何も再生せず終了を通知する"""
        self.stop()
        last = self._last_frame()
        if frame >= last:
            self.finished.emit()
            return
        self._start = self._index = self._next = frame
        reader = FrameReader(self._path, self._info, frame, self._size, self)
        reader.failed.connect(self.failed)
        self._reader = reader
        reader.start()
        self._clock.start()
        self._timer.start()

    def stop(self) -> None:
        """再生を止める(何も再生していなければ何もしない)"""
        self._timer.stop()
        reader, self._reader = self._reader, None
        if reader is None:
            return
        reader.stop()
        reader.wait(STOP_WAIT_MS)

    def _last_frame(self) -> int:
        return max(0, self._info.frame_count - 1)

    def _on_tick(self) -> None:
        reader = self._reader
        if reader is None:
            return
        last = self._last_frame()
        target = target_frame(
            self._start, self._clock.elapsed(), self._info.fps, self._speed, last
        )
        data = None
        index = self._index
        # 目標フレームまで読み捨て、最後の 1 枚だけ表示する(実時間優先のコマ落ち)
        while self._next <= target:
            try:
                data = reader.queue.get_nowait()
            except queue.Empty:
                break
            index = self._next
            self._next += 1
        if data is None:
            if not reader.isRunning() and reader.queue.empty():
                self.stop()
                self.finished.emit()
                return
            # キューが枯れた分だけ時計を張り直し、復帰時にまとめてコマ落ちしないようにする
            self._start = self._index
            self._clock.restart()
            return
        self._index = index
        width, height = self._size
        image = QImage(
            data, width, height, width * 3, QImage.Format.Format_RGB888
        ).copy()
        self.frame_ready.emit(index, image)
        if index >= last:
            self.stop()
            self.finished.emit()
```

- [ ] **Step 4: テストが通ることを確認する**

Run: `python -m pytest tests/test_video_player.py -v`
Expected: PASS

- [ ] **Step 5: コミット**

```bash
git add mosaic_tool/video/player.py tests/test_video_player.py
git commit -m "feat(video): ffmpeg パイプで実時間再生する再生エンジンを追加する"
```

---

### Task 9: キャンバスの再生用描画パス

**Files:**
- Modify: `mosaic_tool/canvas.py`（`MosaicOverlay.paint`、`MosaicCanvas.__init__`、
  `set_image`、`mousePressEvent` の `can_create`、新規メソッド 3 つ）
- Test: `tests/test_canvas.py`

**Interfaces:**
- Produces:
  - `MosaicCanvas.set_playback_image(image: QImage) -> None`
    シーン構成・ズーム・範囲を保ったままフレームを差し替える。プロキシ解像度の画像は
    シーン矩形（元の解像度）へ伸ばして表示する
  - `MosaicCanvas.set_playback_regions(regions: list[Region]) -> None`
    掛かる範囲の集合が変わったときだけアイテムを付け外しする（Undo は積まない）
  - `MosaicCanvas.set_playback_mode(on: bool) -> None`
    範囲の作成・選択・変形を止める（枠線は描いたまま）

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_canvas.py` へ追加（既存の `make_canvas` 相当のヘルパーがあればそれを使う。
無ければ以下を定義する）:

```python
class TestPlayback:
    def _canvas(self):
        from PIL import Image

        QApplication.instance() or QApplication([])
        canvas = MosaicCanvas()
        canvas.set_image(Image.new("RGB", (100, 80), (10, 20, 30)))
        return canvas

    def _proxy(self, width=50, height=40):
        from PySide6.QtGui import QImage

        image = QImage(width, height, QImage.Format.Format_RGB888)
        image.fill(0x336699)
        return image

    def test_playback_image_keeps_the_scene_rect(self):
        canvas = self._canvas()
        before = canvas.sceneRect()
        canvas.set_playback_image(self._proxy())
        assert canvas.sceneRect() == before

    def test_playback_image_keeps_the_zoom(self):
        canvas = self._canvas()
        canvas.scale(2.0, 2.0)
        before = canvas.transform().m11()
        canvas.set_playback_image(self._proxy())
        assert canvas.transform().m11() == before

    def test_playback_image_scales_the_proxy_to_the_image_size(self):
        canvas = self._canvas()
        canvas.set_playback_image(self._proxy(50, 40))
        item = canvas._pixmap_item
        # 50x40 のプロキシを 100x80 のシーン矩形へ伸ばす
        assert item.pixmap().width() == 50
        assert item.sceneBoundingRect().width() == 100

    def test_set_image_resets_the_proxy_scaling(self):
        from PIL import Image

        canvas = self._canvas()
        canvas.set_playback_image(self._proxy(50, 40))
        canvas.set_image(Image.new("RGB", (100, 80)))
        assert canvas._pixmap_item.transform().m11() == 1.0

    def test_playback_regions_add_and_remove_items(self):
        canvas = self._canvas()
        a, b = _rect_region(), _rect_region()
        canvas.set_playback_regions([a, b])
        assert len(canvas.get_regions()) == 2
        canvas.set_playback_regions([a])
        assert [id(r) for r in canvas.get_regions()] == [id(a)]
        canvas.set_playback_regions([])
        assert canvas.get_regions() == []

    def test_playback_regions_do_not_mark_changed(self):
        canvas = self._canvas()
        fired = []
        canvas.regions_changed.connect(lambda: fired.append(True))
        canvas.set_playback_regions([_rect_region()])
        assert fired == []

    def test_playback_regions_are_not_undoable(self):
        canvas = self._canvas()
        canvas.set_playback_regions([_rect_region()])
        assert canvas._undo_stack == []

    def test_playback_mode_blocks_selection(self):
        canvas = self._canvas()
        region = _rect_region()
        canvas.set_playback_regions([region])
        canvas.set_playback_mode(True)
        canvas.select_regions([region])
        assert canvas.selected_regions() == []
        canvas.set_playback_mode(False)
        canvas.select_regions([region])
        assert canvas.selected_regions() == [region]
```

`_rect_region` が未定義なら、テストファイルのヘルパーとして定義する:

```python
def _rect_region() -> Region:
    return Region(kind=RegionKind.RECT, rect=QRectF(0, 0, 10, 10))
```

- [ ] **Step 2: テストが失敗することを確認する**

Run: `python -m pytest tests/test_canvas.py::TestPlayback -v`
Expected: FAIL（`MosaicCanvas` に `set_playback_image` が無い）

- [ ] **Step 3: 最小の実装を書く**

`MosaicOverlay.paint` を差し替える（プロキシ解像度のモザイクにも対応する）:

```python
    def paint(self, painter, option, widget=None):
        if self._mosaic_pixmap is None or self._clip.isEmpty():
            return
        painter.setClipPath(self._clip)
        if self._mosaic_pixmap.size() != self._rect.size().toSize():
            # 再生中はプロキシ解像度の pixmap が来る。マス目をぼかさないよう
            # 補間を切ってシーン矩形へ伸ばす
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
            painter.drawPixmap(
                self._rect, self._mosaic_pixmap, QRectF(self._mosaic_pixmap.rect())
            )
            return
        painter.drawPixmap(0, 0, self._mosaic_pixmap)
```

`MosaicCanvas.__init__` の状態へ追加:

```python
        self._playback = False   # 再生中か(範囲の作成・選択・変形を止める)
```

`set_image` の `self._pixmap_item = self._scene.addPixmap(pm)` の直後へ追加:

```python
        # 再生で付いたプロキシの拡大を戻す(原寸の pixmap をそのまま表示する)
        self._pixmap_item.setTransform(QTransform())
```

`mousePressEvent` の `can_create` へ再生中の条件を足す:

```python
        can_create = (
            event.button() == Qt.MouseButton.LeftButton
            and not self._preview
            and not self._playback
            and self._is_on_image(event.position().toPoint())
        )
```

`set_preview_mode` の下へ 3 つのメソッドを追加:

```python
    def set_playback_image(self, image: QImage) -> None:
        """再生中のフレームを差し替える(シーン構成・ズーム・範囲は保つ)

        set_image はシーンを作り直し表示倍率も戻すため毎フレームには使えない。
        プロキシ解像度の画像はアイテムの変換でシーン矩形へ伸ばす(拡大縮小した
        pixmap を作り直すより軽い)。
        """
        if self._pixmap_item is None or self._image is None:
            return
        self._pixmap_item.setPixmap(QPixmap.fromImage(image))
        width, height = self._image.size
        transform = QTransform()
        if image.width() != width or image.height() != height:
            transform.scale(width / image.width(), height / image.height())
        self._pixmap_item.setTransform(transform)
        if self._preview:
            self._rebuild_playback_mosaic(image)

    def _rebuild_playback_mosaic(self, image: QImage) -> None:
        """再生中のモザイクをプロキシ解像度で作る

        ブロックサイズはプロキシ倍率で換算した近似値になる(正確な仕上がりは
        静止状態のプレビューと書き出しで確認する)。
        """
        if self._overlay is None or self._image is None:
            return
        scale = image.width() / self._image.size[0]
        block = max(1, round(self._block * scale))
        if block <= 1:
            self._overlay.set_mosaic(QPixmap.fromImage(image))
            return
        small = image.scaled(
            max(1, image.width() // block),
            max(1, image.height() // block),
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        mosaic = small.scaled(
            image.size(),
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.FastTransformation,
        )
        self._overlay.set_mosaic(QPixmap.fromImage(mosaic))

    def set_playback_regions(self, regions: list[Region]) -> None:
        """再生中に表示する範囲を差し替える(Undo は積まず、未保存扱いにもしない)

        クリップ形状の作り直しは重いため、掛かる範囲の集合が変わったときだけ行う。
        """
        wanted = {id(r): r for r in regions}
        current = {id(item.region): item for item in self._region_items()}
        if wanted.keys() == current.keys():
            return
        # regions_changed を出さないよう読み込み中として扱う
        self._loading = True
        try:
            for key, item in current.items():
                if key not in wanted:
                    self._scene.removeItem(item)
            for key, region in wanted.items():
                if key not in current:
                    self.add_region(region, push_undo=False)
        finally:
            self._loading = False
        self._update_clip()

    def set_playback_mode(self, on: bool) -> None:
        """再生中の切替(範囲の作成・選択・変形を止める。枠線は描いたまま)"""
        self._playback = on
        if on:
            self._scene.clearSelection()
        for item in self._region_items():
            item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, not on)
            item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, not on)
```

`add_region` は再生中に呼ばれてもフラグが立たないよう、末尾へ追加:

```python
        if self._playback:
            item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, False)
            item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, False)
```

（`add_region` の `return item` の直前へ置く）

- [ ] **Step 4: テストが通ることを確認する**

Run: `python -m pytest tests/test_canvas.py -v`
Expected: PASS

- [ ] **Step 5: コミット**

```bash
git add mosaic_tool/canvas.py tests/test_canvas.py
git commit -m "feat(canvas): 再生中のフレーム差し替えと操作停止に対応する"
```

---

### Task 10: 再生を app へ組み込む

**Files:**
- Modify: `mosaic_tool/app.py`（import、`__init__`、`_build_toolbar`、`_open_video`、
  `_leave_video_mode`、`_reject_while_video_busy`、`closeEvent`、新規ハンドラ）
- Modify: `mosaic_tool/video/timeline_window.py`（Space の中継）
- Test: `tests/test_app.py`、`tests/test_video_timeline_window.py`

**Interfaces:**
- Consumes: Task 5 の `TimelineBar.play_clicked` / `set_playing` / `speed`、
  Task 8 の `VideoPlayer`、Task 9 の `canvas.set_playback_image` /
  `set_playback_regions` / `set_playback_mode`
- Produces:
  - `MainWindow._player: VideoPlayer | None`
  - `MainWindow._toggle_playback()` — 再生中なら止め、そうでなければ現在フレームから再生する
  - `TimelineArea.playback_toggle_requested: Signal()` と
    `TimelineWindow.playback_toggle_requested: Signal()`（Space の中継）

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_video_timeline_window.py` へ追加:

```python
def test_space_requests_playback_toggle():
    from PySide6.QtGui import QKeyEvent

    area = make_area()
    fired = []
    area.playback_toggle_requested.connect(lambda: fired.append(True))
    area.keyPressEvent(
        QKeyEvent(
            QKeyEvent.Type.KeyPress, Qt.Key.Key_Space, Qt.KeyboardModifier.NoModifier
        )
    )
    assert fired == [True]
```

`tests/test_app.py` へ追加:

```python
class TestPlayback:
    @pytest.fixture
    def player(self, video, monkeypatch):
        """VideoPlayer を差し替えて再生の配線だけを見る

        app 側が frame_ready 等へ接続するため、シグナルは本物と同じ定義を持たせる。
        """
        from PySide6.QtCore import QObject, Signal
        from PySide6.QtGui import QImage

        events = []

        class FakePlayer(QObject):
            frame_ready = Signal(int, QImage)
            finished = Signal()
            failed = Signal(str)

            def __init__(self, path, info, parent=None):
                super().__init__(parent)
                self.started: list[int] = []
                self.stopped = 0
                self.speed = 1.0
                self._playing = False
                events.append(self)

            def is_playing(self):
                return self._playing

            def start(self, frame):
                self.started.append(frame)
                self._playing = True

            def stop(self):
                self.stopped += 1
                self._playing = False

            def set_speed(self, speed):
                self.speed = speed

        monkeypatch.setattr("mosaic_tool.app.VideoPlayer", FakePlayer)
        return events

    def test_play_button_starts_from_the_current_frame(self, video, player):
        video._timeline.seek(20)
        video._timeline.play_clicked.emit()
        assert player[0].started == [20]

    def test_play_button_stops_while_playing(self, video, player):
        video._timeline.play_clicked.emit()
        video._timeline.play_clicked.emit()
        assert player[0].stopped >= 1
        assert not video._player.is_playing()

    def test_button_text_follows_the_state(self, video, player):
        from mosaic_tool.video.timeline import PAUSE_TEXT, PLAY_TEXT

        video._timeline.play_clicked.emit()
        assert video._timeline._play_btn.text() == PAUSE_TEXT
        video._timeline.play_clicked.emit()
        assert video._timeline._play_btn.text() == PLAY_TEXT

    def test_speed_change_is_forwarded(self, video, player):
        video._timeline.play_clicked.emit()
        video._timeline._speed_combo.setCurrentIndex(0)
        assert player[0].speed == 0.25

    def test_timeline_window_space_toggles_playback(self, video, player):
        video._timeline_window.playback_toggle_requested.emit()
        assert player[0].started == [0]

    def test_playback_stops_before_detect(self, video, player, monkeypatch):
        class FakeDialog:
            def __init__(self, *a, **k):
                pass

            def exec(self):
                return QDialog.DialogCode.Rejected

        monkeypatch.setattr("mosaic_tool.app.DetectRangeDialog", FakeDialog)
        video._timeline.play_clicked.emit()
        video._start_video_detect({"m.pt": {"conf": 0.5, "classes": []}})
        assert not video._player.is_playing()

    def test_playback_stops_when_leaving_video_mode(self, video, player):
        video._timeline.play_clicked.emit()
        video._leave_video_mode()
        assert player[0].stopped >= 1
        assert video._player is None

    def test_frame_ready_updates_the_state(self, video, player):
        from PySide6.QtGui import QImage

        image = QImage(32, 24, QImage.Format.Format_RGB888)
        image.fill(0)
        video._timeline.play_clicked.emit()
        video._on_playback_frame(15, image)
        assert video._video.frame == 15
        assert video._timeline.frame() == 15
        assert video._timeline_window._area._frame == 15
```

- [ ] **Step 2: テストが失敗することを確認する**

Run: `python -m pytest tests/test_app.py::TestPlayback tests/test_video_timeline_window.py -v`
Expected: FAIL（`mosaic_tool.app` に `VideoPlayer` が無い）

- [ ] **Step 3: 最小の実装を書く**

`mosaic_tool/video/timeline_window.py` — `TimelineArea` のシグナルへ追加:

```python
    playback_toggle_requested = Signal()  # Space による再生/一時停止の要求
```

`TimelineArea.keyPressEvent` の先頭へ追加:

```python
        if event.key() == Qt.Key.Key_Space:
            self.playback_toggle_requested.emit()
            event.accept()
            return
```

`TimelineWindow` のシグナルへ `playback_toggle_requested = Signal()` を追加し、
`__init__` の配線へ:

```python
        self._area.playback_toggle_requested.connect(self.playback_toggle_requested)
```

`mosaic_tool/app.py` の import へ追加:

```python
from mosaic_tool.video.player import VideoPlayer
```

`__init__` の動画モードの状態へ追加:

```python
        # 再生の状態(None なら未再生。動画を閉じるまで使い回す)
        self._player: VideoPlayer | None = None
```

`__init__` のタイムライン配線（`frame_changed` の接続の隣）へ追加:

```python
        self._timeline.play_clicked.connect(self._toggle_playback)
        self._timeline.speed_changed.connect(self._on_playback_speed_changed)
```

`_build_toolbar` のプレビューの隣へ、Space のショートカット用アクションを追加する
（ツールバーには載せず、キャンバスのショートカットとしてだけ使う）:

```python
        # 再生/一時停止。ツールバーには出さず Space のショートカットとしてだけ持つ
        self._playback_act = QAction("再生", self)
        self._add_shortcut(self._playback_act, QKeySequence(Qt.Key.Key_Space))
        self._playback_act.triggered.connect(self._toggle_playback)
```

`_ensure_timeline_window` の配線へ追加:

```python
            window.playback_toggle_requested.connect(self._toggle_playback)
```

`_leave_video_mode` の先頭（`self._video = None` の前）へ追加:

```python
        self._stop_playback()
        self._player = None
```

`_reject_while_video_busy` の先頭へ追加:

```python
        # 再生中の操作は再生を止めてから通す(書き出し・検出とは違い待たせない)
        self._stop_playback()
```

`_open_video` の `self._show_frame(0)` の前へ追加:

```python
        self._player = None
```

`closeEvent` の `self._cleanup_video_detect()` の隣へ追加:

```python
            self._stop_playback()
```

`# --- タイムラインウィンドウ ---` の直前へ再生の一式を追加:

```python
    # --- 再生 ---

    def _ensure_player(self) -> VideoPlayer | None:
        """再生エンジンを遅延生成して返す(動画モードでなければ None)"""
        video = self._video
        if video is None:
            return None
        if self._player is None:
            player = VideoPlayer(video.path, video.info, self)
            player.frame_ready.connect(self._on_playback_frame)
            player.finished.connect(self._on_playback_finished)
            player.failed.connect(self._on_playback_failed)
            self._player = player
        return self._player

    def _toggle_playback(self) -> None:
        """再生中なら止め、そうでなければ現在フレームから再生する"""
        video = self._video
        if video is None or self._exporter is not None or self._video_detect is not None:
            return
        player = self._ensure_player()
        if player is None:
            return
        if player.is_playing():
            self._stop_playback()
            return
        # 表示中フレームでの編集を区間リストへ反映してから再生へ移る
        self._sync_video_regions()
        self.canvas.set_playback_mode(True)
        player.set_speed(self._timeline.speed())
        self._timeline.set_playing(True)
        player.start(video.frame)

    def _stop_playback(self) -> None:
        """再生を止めて編集できる状態(原寸フレーム)へ戻す"""
        player = self._player
        if player is None or not player.is_playing():
            return
        player.stop()
        self._timeline.set_playing(False)
        self.canvas.set_playback_mode(False)
        if self._video is not None:
            # プロキシ解像度のまま編集させないよう原寸で描き直す
            self._show_frame(self._video.frame)

    def _on_playback_frame(self, frame: int, image) -> None:
        """再生中の 1 フレームを表示し、再生ヘッドを進める"""
        video = self._video
        if video is None:
            return
        video.frame = frame
        self.canvas.set_playback_regions(video.regions_at(frame))
        self.canvas.set_playback_image(image)
        self._timeline.set_frame(frame)
        if self._timeline_window is not None:
            self._timeline_window.set_frame(frame)

    def _on_playback_finished(self) -> None:
        self._stop_playback()

    def _on_playback_failed(self, message: str) -> None:
        self._stop_playback()
        QMessageBox.warning(self, "再生エラー", message)
```

`_on_playback_frame` は `_timeline.set_frame`（シグナルを出さない経路）を使うため、
`_on_frame_changed` は走らず ffmpeg の 1 枚取り出しも起きない。

- [ ] **Step 4: テストが通ることを確認する**

Run: `python -m pytest tests/ -v`
Expected: PASS（全テスト）

- [ ] **Step 5: 手で動作を確認する**

```bash
just run
```

動画をドロップして次を確認する:
1. ▶ で再生され、下部バーとタイムラインの再生ヘッドが進む
2. Tab（プレビュー）を ON にして再生すると、モザイクが掛かった状態で動く
3. 速度を 0.25x / 2x に変えると再生速度が変わる
4. ⏸ で止めた位置のフレームがはっきり表示され、範囲を編集できる
5. Space がキャンバス・タイムラインウィンドウのどちらにフォーカスがあっても効く
6. 再生中にホイールで横スクロール、Ctrl+ホイールでズームができる
7. 縦線が細かく入り、区間バーが埋もれていない

- [ ] **Step 6: コミット**

```bash
git add mosaic_tool/app.py mosaic_tool/video/timeline_window.py tests/
git commit -m "feat(video): 動画の再生モードを追加する"
```

---

### Task 11: README の操作説明を更新する

**Files:**
- Modify: `README.md:25-60`（操作表・ショートカット表）、`README.md:130-175`（動画の節）

**Interfaces:**
- Consumes: Task 1〜10 で確定した操作（検出範囲ダイアログ、再生、ホイール、副目盛り）

- [ ] **Step 1: 操作表とショートカット表を更新する**

「操作」表へ 1 行追加する:

```markdown
| 再生 | 動画モードのタイムラインの ▶ / Space(速度は隣のコンボで選択) |
```

「ショートカットキー」表へ 1 行追加する（`Tab` の下）:

```markdown
| Space | 再生 / 一時停止(動画モードのみ) |
```

- [ ] **Step 2: 動画の節の手順を更新する**

手順 1 を差し替える:

```markdown
1. 動画を開くとキャンバス下にタイムラインが出ます。スライダーや `←` / `→` で
   フレームを移動できます。`▶` (または `Space`) で再生し、隣のコンボで速度
   (0.25x / 0.5x / 1x / 2x) を選べます。再生はプレビュー (`Tab`) の状態に従い、
   モザイクを掛けた状態でも確認できます (再生中の映像は表示用に縮小した近似で、
   範囲の編集は止まります)
```

タイムラインウィンドウの操作表へ 2 行追加する:

```markdown
   | ホイール | 横方向のスクロール |
   | `Shift` + ホイール | 縦方向のスクロール (行が多いとき) |
```

手順 3 と 4 を差し替える（検出間隔の置き場所が変わったため）:

```markdown
3. 「自動検出」を実行すると範囲を指定するダイアログが出ます。開始・終了フレーム
   (既定は現在のフレームから末尾まで) と検出間隔を決めると、その範囲を走査し、
   検出 1 件ごとに [検出フレーム, 次の検出フレームの直前] の区間つき範囲を追加します
   (フレームごとに独立で、別フレームの検出結果には影響されません。
   セグメンテーションモデルは輪郭の多角形、検出のみのモデルは矩形になります)
4. 検出間隔を増やすと、間引いて検出して高速化できます
   (漏れやすくなるため、動きの少ない動画向け)
```

「制限事項」へ 1 行追加する:

```markdown
- 再生は映像のみで音声は出ません
```

- [ ] **Step 3: 記述とコードの食い違いがないか確認する**

Run: `python -m pytest tests/ -v`
Expected: PASS（README の変更はテストに影響しないが、全体が緑であることを確かめる）

README を読み直し、次を確認する:
- 「検出間隔」を下部バーの機能として説明している箇所が残っていないこと
- ショートカット表の `Space` と本文の説明が一致していること

- [ ] **Step 4: コミット**

```bash
git add README.md
git commit -m "docs: 動画モードの再生と検出範囲の操作説明を追加する"
```

---

## Self-Review

**1. Spec coverage**

| 設計の項目 | 対応タスク |
| --- | --- |
| 検出範囲ダイアログ（既定値・相互クランプ・件数表示） | Task 3 |
| 純関数 `format_timecode` / `detect_frame_count` | Task 3 |
| 検出間隔の下部バーからの移設 | Task 3（新設）/ Task 5（撤去） |
| ffmpeg の範囲対応とフレーム番号の対応付け | Task 1 / Task 4 |
| `merge_detections` の範囲クランプ | Task 4 |
| ホイールの分岐（横スクロール / Ctrl ズーム / Shift 縦） | Task 6 |
| 副目盛りと縦線（`_minor_interval` / `MIN_MINOR_PX` / 描画順） | Task 7 |
| `FrameReader` / `VideoPlayer` / 純関数 | Task 8 |
| プロキシサイズと再生用コマンド | Task 2 |
| `set_playback_image` / `set_playback_mode` / クリップ再計算の間引き | Task 9 |
| 再生の統合・排他・停止時の原寸復帰 | Task 10 |
| 操作 UI（▶⏸・速度コンボ・Space の 2 経路） | Task 5 / Task 10 |
| ドキュメント更新 | Task 11 |

**2. Placeholder scan** — 「適切に」「必要に応じて」等の曖昧な指示は無し。各ステップに実コードを記載済み。

**3. Type consistency**

- `extract_frames_command(..., start=, count=)` — Task 1 で定義、Task 4 で使用（一致）
- `proxy_size` / `playback_command` — Task 2 で定義、Task 8 で使用（一致）
- `detect_frame_count(start, end, step)` — Task 3 で定義、Task 4 で使用（一致）
- `DetectRangeDialog(total_frames, fps, current_frame, step, parent)` / `range_result()` —
  Task 3 で定義、Task 4 で使用（一致）
- `TimelineBar.play_clicked` / `set_playing` / `speed` / `speed_changed` —
  Task 5 で定義、Task 10 で使用（一致）
- `VideoPlayer.frame_ready(int, QImage)` / `start` / `stop` / `is_playing` / `set_speed` —
  Task 8 で定義、Task 10 で使用（一致）
- `canvas.set_playback_image` / `set_playback_regions` / `set_playback_mode` —
  Task 9 で定義、Task 10 で使用（一致）
- `playback_toggle_requested` — Task 10 で `TimelineArea` と `TimelineWindow` の両方に定義（一致）
