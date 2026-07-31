"""MainWindow のプレビュー操作(Tab ショートカット・画像切替時の解除)の検証"""
import io as std_io
import os
from pathlib import Path

import pytest
from PySide6.QtCore import QProcess, QRectF, QSettings, Qt
from PySide6.QtGui import QImage, QKeySequence

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QDialog,
    QMessageBox,
)

from PIL import Image  # noqa: E402

from mosaic_tool.app import PEN_STEP, THRESHOLD_STEP, MainWindow  # noqa: E402
from mosaic_tool.regions import Region, RegionKind  # noqa: E402
from mosaic_tool.settings import AppSettings  # noqa: E402
from mosaic_tool.video import ffmpeg as video_ffmpeg  # noqa: E402
from mosaic_tool.video.ffmpeg import VideoInfo  # noqa: E402
from mosaic_tool.video.frame_fetcher import FrameFetcher  # noqa: E402
from mosaic_tool.video.scrubber import Scrubber  # noqa: E402
from mosaic_tool.video.session import VideoRegion  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def runtime_ready(monkeypatch):
    """推論環境ありを既定にする

    未構築だと自動検出がセットアップダイアログを開いて止まるため。
    未構築の挙動を見るテストは各自で False へ上書きする。
    """
    monkeypatch.setattr("mosaic_tool.app.detect_paths.is_runtime_ready", lambda: True)


@pytest.fixture
def window(qapp, tmp_path):
    """実設定を汚さないよう一時 ini を使い、画像 2 枚を開いたウィンドウを返す"""
    images = []
    for i in range(2):
        path = tmp_path / f"img{i}.png"
        Image.new("RGB", (40, 30), (i * 100, 0, 0)).save(path)
        images.append(path)
    settings = AppSettings(
        QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    )
    win = MainWindow([str(p) for p in images], settings=settings)
    yield win
    win.close()


def test_preview_shortcut_is_tab(window):
    assert window._preview_act.shortcut() == QKeySequence(Qt.Key.Key_Tab)


def test_toolbar_actions_show_key_in_tooltip(window):
    # キーの表記は OS で異なる(macOS は ⌘S / ⇥ のような記号)ため、
    # 期待値も Qt のネイティブ表記から組み立てる
    def key_text(key) -> str:
        return QKeySequence(key).toString(QKeySequence.SequenceFormat.NativeText)

    expected = {
        "ペン": f"ペン ({key_text(Qt.Key.Key_1)})",
        "矩形": f"矩形 ({key_text(Qt.Key.Key_2)})",
        "◀ 前へ": f"◀ 前へ ({key_text(Qt.Key.Key_Left)})",
        "次へ ▶": f"次へ ▶ ({key_text(Qt.Key.Key_Right)})",
        "保存": f"保存 ({key_text(QKeySequence.StandardKey.Save)})",
        "プレビュー": f"プレビュー ({key_text(Qt.Key.Key_Tab)})",
        "自動検出": f"自動検出 ({key_text(Qt.Key.Key_D)})",
        # ショートカットを持たないため説明文をそのまま出す
        "タイムライン": "タイムラインウィンドウを表示する(動画モードのみ)",
    }
    tooltips = {
        act.text(): act.toolTip()
        for act in window._toolbar.actions()
        if act.text()
    }
    assert tooltips == expected


def test_toolbar_wraps_when_width_is_not_enough(window):
    """横幅が足りないときは項目を折り返して 2 行以上で表示する"""
    layout = window._toolbar.layout()
    wide = layout.heightForWidth(2000)
    narrow = layout.heightForWidth(400)
    assert narrow > wide


def test_single_key_shortcuts_are_scoped_to_canvas(window):
    """修飾キーなしのキーがスピンボックスへの入力を奪わないこと(保存のみ全体)"""
    scoped = Qt.ShortcutContext.WidgetWithChildrenShortcut
    for act in (window._preview_act, window._prev_act, window._next_act):
        assert act.shortcutContext() == scoped
        assert act in window.canvas.actions()
    for act in window._mode_group.actions():
        assert act.shortcutContext() == scoped
    save_act = next(
        a for a in window._toolbar.actions() if a.text() == "保存"
    )
    assert save_act.shortcutContext() == Qt.ShortcutContext.WindowShortcut
    assert save_act not in window.canvas.actions()


def test_spinboxes_step_by_five_but_accept_any_value(window):
    """矢印ボタンは 5 刻み、数値入力は 1 刻みで受け付ける"""
    assert window._threshold_spin.singleStep() == THRESHOLD_STEP
    assert window._pen_spin.singleStep() == PEN_STEP

    window._threshold_spin.setValue(13)
    assert window._threshold_spin.value() == 13
    assert window.canvas._threshold == pytest.approx(0.13)

    window._pen_spin.setValue(37)
    assert window._pen_spin.value() == 37
    assert window.canvas._pen_width == pytest.approx(37.0)


def test_preview_toggle_updates_canvas(window):
    window._preview_act.setChecked(True)
    assert window.canvas._preview
    window._preview_act.setChecked(False)
    assert not window.canvas._preview


def test_preview_cleared_on_navigation(window):
    window._preview_act.setChecked(True)
    window._go(1)
    assert window._index == 1
    assert not window._preview_act.isChecked()
    assert not window.canvas._preview


def test_detect_action_shortcut_is_d(window):
    assert window._detect_act.shortcut() == QKeySequence(Qt.Key.Key_D)


def test_detected_regions_are_added_to_canvas(window):
    window._on_detected([{"bbox": [0, 0, 10, 10]}, {"bbox": [20, 0, 30, 10]}])
    assert len(window.canvas.get_regions()) == 2


def test_detected_regions_can_be_undone_at_once(window):
    window._on_detected([{"bbox": [0, 0, 10, 10]}, {"bbox": [20, 0, 30, 10]}])
    window.canvas.undo()
    assert window.canvas.get_regions() == []


def test_empty_detection_shows_message(window):
    window._on_detected([])
    assert "追加する範囲はありませんでした" in window.statusBar().currentMessage()


def test_detecting_twice_does_not_duplicate_the_same_region(window):
    detections = [{"bbox": [0, 0, 10, 10]}, {"bbox": [20, 0, 30, 10]}]
    window._on_detected(detections)
    window._on_detected(detections)
    assert len(window.canvas.get_regions()) == 2
    assert "追加する範囲はありませんでした" in window.statusBar().currentMessage()


def test_detect_failure_shows_error(window, monkeypatch):
    shown = []
    monkeypatch.setattr(
        "mosaic_tool.app.QMessageBox.critical",
        lambda *args, **kwargs: shown.append(args[2]),
    )
    window._on_detect_failed("モデルの読み込みに失敗しました")
    assert shown and "モデルの読み込み" in shown[0]


def test_toolbar_has_no_confidence_spinbox(window):
    # 信頼度はモデルごとの設定に一本化した
    assert not hasattr(window, "_confidence_spin")


def test_detect_action_opens_the_window(window):
    window._detect_act.trigger()
    assert window._detect_window is not None
    assert window._detect_window.isVisible() is True
    window._detect_window.close()


def test_detect_window_is_reused(window):
    window._detect_act.trigger()
    first = window._detect_window
    window._detect_act.trigger()  # 一度閉じる
    window._detect_act.trigger()  # 開き直しても同じインスタンス
    assert window._detect_window is first
    first.close()


def test_detect_action_toggles_the_window(window):
    window._detect_act.trigger()
    assert window._detect_act.isChecked() is True
    assert window._detect_window.isVisible() is True
    window._detect_act.trigger()
    assert window._detect_act.isChecked() is False
    assert window._detect_window.isVisible() is False


def test_closing_the_detect_window_unchecks_the_action(window):
    window._detect_act.trigger()
    window._detect_window.close()
    assert window._detect_act.isChecked() is False


def test_detect_window_learns_whether_an_image_is_open(window):
    window._detect_act.trigger()
    # フィクスチャは画像 2 枚を開いた状態
    assert window._detect_window._image_available is True
    window._detect_window.close()


def test_start_detect_sends_the_models_to_the_worker(window, monkeypatch):
    sent = []
    monkeypatch.setattr(
        window._worker,
        "request",
        lambda image, models, device: sent.append((image, models, device)),
    )
    window._detect_act.trigger()
    window._start_detect({"a.pt": {"conf": 0.25, "classes": ["face"]}})
    assert sent and sent[0][1] == {"a.pt": {"conf": 0.25, "classes": ["face"]}}
    window._detect_window.close()


def test_request_detect_uses_resolved_device(window, monkeypatch):
    """設定値そのままではなく、OS ごとに解決した device をワーカーへ渡すこと"""
    monkeypatch.setattr("mosaic_tool.app.resolve_device", lambda setting: "mps")
    sent = []
    monkeypatch.setattr(
        window._worker, "request", lambda image, models, device: sent.append(device)
    )
    window._request_detect({"a.pt": {"conf": 0.25, "classes": ["face"]}})
    assert sent == ["mps"]


def test_class_request_is_forwarded_to_the_worker(window, monkeypatch):
    """ウィンドウのクラス要求がワーカーへ渡り、応答がウィンドウへ返る"""
    calls = []
    monkeypatch.setattr(
        window._worker, "request_classes", lambda: calls.append("requested")
    )
    window._detect_act.trigger()
    detect_window = window._detect_window
    detect_window.classes_requested.emit()
    assert calls == ["requested"]

    received = []
    monkeypatch.setattr(detect_window, "show_class_selection", received.append)
    window._worker.classes_received.emit({"m.pt": ["face"]})
    assert received == [{"m.pt": ["face"]}]
    detect_window.close()


def test_detect_failure_cancels_a_pending_class_request(window, monkeypatch):
    monkeypatch.setattr(
        "mosaic_tool.app.QMessageBox.critical", lambda *args, **kwargs: None
    )
    window._detect_act.trigger()
    detect_window = window._detect_window
    cancelled = []
    monkeypatch.setattr(
        detect_window, "cancel_class_request", lambda: cancelled.append(1)
    )
    window._on_detect_failed("失敗")
    assert cancelled == [1]
    detect_window.close()


@pytest.fixture
def batch(window, monkeypatch):
    """全ファイル実行を確認ダイアログなしで走らせ、依頼先の画像を記録する"""
    monkeypatch.setattr(
        "mosaic_tool.app.QMessageBox.question",
        lambda *args, **kwargs: QMessageBox.StandardButton.Yes,
    )
    requested = []
    monkeypatch.setattr(
        window._worker,
        "request",
        lambda image, models, device: requested.append(image),
    )
    return requested


def test_detect_all_asks_before_running(window, monkeypatch):
    asked = []

    def fake_question(*args, **kwargs):
        asked.append(args[2])
        return QMessageBox.StandardButton.No

    monkeypatch.setattr("mosaic_tool.app.QMessageBox.question", fake_question)
    monkeypatch.setattr(window._worker, "request", lambda *a: pytest.fail("実行された"))
    window._start_detect_all({"a.pt": 0.25})
    # 断ったら何も実行しない
    assert asked and "2 件" in asked[0]
    assert window._batch_models is None


def test_detect_all_walks_every_image_and_saves(window, batch, tmp_path):
    window._start_detect_all({"a.pt": 0.25})
    assert batch == [str(tmp_path / "img0.png")]
    window._on_detected([{"bbox": [0, 0, 10, 10]}])
    # 1 枚目を保存して次の画像へ進む
    assert (tmp_path / "img0_mc.png").exists()
    assert window._index == 1
    assert batch[-1] == str(tmp_path / "img1.png")
    window._on_detected([{"bbox": [0, 0, 10, 10]}])
    assert (tmp_path / "img1_mc.png").exists()
    # 最後まで終わったら通常の状態へ戻る
    assert window._batch_models is None
    assert "2 件の画像を保存しました" in window.statusBar().currentMessage()


def test_detect_all_keeps_the_window_running_until_it_ends(window, batch):
    window._detect_act.trigger()
    window._start_detect_all({"a.pt": 0.25})
    window._on_detected([])
    assert window._detect_window._running is True
    window._on_detected([])
    assert window._detect_window._running is False
    window._detect_window.close()


def test_detect_all_shows_progress_by_processed_files(window, batch):
    """全ファイル実行の進捗は処理済みファイル数 / 全ファイル数で出す"""
    window._detect_act.trigger()
    bar = window._detect_window._bar
    window._start_detect_all({"a.pt": 0.25})
    assert (bar.value(), bar.maximum()) == (0, 2)
    # モデル単位の進捗では上書きしない
    window._on_detect_progress(1, 3, "a.pt")
    assert (bar.value(), bar.maximum()) == (0, 2)
    window._on_detected([])
    assert (bar.value(), bar.maximum()) == (1, 2)
    window._detect_window.close()


def test_detect_all_stops_on_failure(window, batch, monkeypatch):
    monkeypatch.setattr(
        "mosaic_tool.app.QMessageBox.critical", lambda *args, **kwargs: None
    )
    window._start_detect_all({"a.pt": 0.25})
    window._on_detect_failed("検出に失敗しました")
    assert window._batch_models is None
    # 中断後は次の画像を要求しない
    assert len(batch) == 1


def test_navigation_is_blocked_during_detect_all(window, batch):
    window._start_detect_all({"a.pt": 0.25})
    window._go(1)
    assert window._index == 0


def test_opening_files_is_blocked_during_detect_all(window, batch, tmp_path):
    # 応答待ちの間に対象が差し替わると別の画像へ結果を書き込んでしまう
    other = tmp_path / "other.png"
    Image.new("RGB", (10, 10)).save(other)
    window._start_detect_all({"a.pt": 0.25})
    window.open_paths([other])
    assert len(window._images) == 2


def test_manual_save_is_blocked_during_detect_all(window, batch, tmp_path):
    window._start_detect_all({"a.pt": 0.25})
    window._save_current()
    assert not (tmp_path / "img0_mc.png").exists()


def test_detect_failure_restores_the_window(window, monkeypatch):
    monkeypatch.setattr(
        "mosaic_tool.app.QMessageBox.critical", lambda *args, **kwargs: None
    )
    window._detect_act.trigger()
    window._detect_window.set_running(True)
    window._on_detect_failed("検出に失敗しました")
    assert window._detect_window._running is False
    window._detect_window.close()


def test_closing_the_main_window_closes_the_detect_window(window):
    window._detect_act.trigger()
    detect_window = window._detect_window
    window.close()
    assert detect_window.isVisible() is False


def test_detect_action_opens_setup_first_when_runtime_is_missing(window, monkeypatch):
    # 未構築なら検出ウィンドウより先にセットアップを出す
    monkeypatch.setattr("mosaic_tool.app.detect_paths.is_runtime_ready", lambda: False)
    opened = []

    class FakeSetupDialog:
        def __init__(self, parent=None):
            opened.append(True)

        def exec(self):
            return QDialog.DialogCode.Rejected

    monkeypatch.setattr("mosaic_tool.app.RuntimeSetupDialog", FakeSetupDialog)
    window._detect_act.trigger()
    assert opened
    # セットアップを断ったら検出ウィンドウは出さない(構築前は何もできない)
    assert window._detect_window is None
    assert window._detect_act.isChecked() is False


def test_detect_window_opens_after_a_successful_setup(window, monkeypatch):
    monkeypatch.setattr("mosaic_tool.app.detect_paths.is_runtime_ready", lambda: False)

    class FakeSetupDialog:
        def __init__(self, parent=None):
            pass

        def exec(self):
            return QDialog.DialogCode.Accepted

    monkeypatch.setattr("mosaic_tool.app.RuntimeSetupDialog", FakeSetupDialog)
    window._detect_act.trigger()
    assert window._detect_window is not None
    window._detect_window.close()


def test_setup_is_not_shown_when_the_runtime_is_ready(window, monkeypatch):
    monkeypatch.setattr("mosaic_tool.app.detect_paths.is_runtime_ready", lambda: True)
    opened = []
    monkeypatch.setattr(
        "mosaic_tool.app.RuntimeSetupDialog", lambda parent=None: opened.append(True)
    )
    window._detect_act.trigger()
    assert opened == []
    window._detect_window.close()


class SyncFrameFetcher(FrameFetcher):
    """テストを決定的にするため、要求を同じスレッドで即座に処理するフェッチャー"""

    def start(self):  # スレッドは立てない
        pass

    def request(self, frame):
        try:
            data = video_ffmpeg.extract_frame(self._path, frame, self._info)
        except video_ffmpeg.VideoError as e:
            self.failed.emit(frame, str(e))
            return
        self.frame_ready.emit(frame, data)


class SyncScrubber(Scrubber):
    """テストを決定的にするため、プロキシフレームを同じスレッドで即座に返す"""

    def start(self):  # スレッドは立てない
        pass

    def request(self, frame):
        width, height = self._size
        image = QImage(width, height, QImage.Format.Format_RGB888)
        image.fill(0)
        self.frame_ready.emit(frame, image)


class InstantSettleTimer:
    """シーク静定のデバウンスを即時発火させるタイマーの代役"""

    def __init__(self, fire):
        self._fire = fire

    def start(self):
        self._fire()

    def stop(self):
        pass


@pytest.fixture
def video(window, monkeypatch, tmp_path):
    """ffmpeg をモックして動画モードへ入れる"""
    info = VideoInfo(64, 48, 30.0, "30/1", 100, 3.3, None)
    buf = std_io.BytesIO()
    Image.new("RGB", (64, 48)).save(buf, "PNG")
    monkeypatch.setattr(
        "mosaic_tool.app.video_ffmpeg.is_ffmpeg_ready", lambda: True
    )
    monkeypatch.setattr("mosaic_tool.app.video_ffmpeg.probe", lambda p: info)
    monkeypatch.setattr(
        "mosaic_tool.app.video_ffmpeg.extract_frame",
        lambda *a, **k: buf.getvalue(),
    )
    monkeypatch.setattr("mosaic_tool.app.FrameFetcher", SyncFrameFetcher)
    monkeypatch.setattr("mosaic_tool.app.Scrubber", SyncScrubber)
    monkeypatch.setattr(
        window, "_settle_timer", InstantSettleTimer(window._on_seek_settled)
    )
    path = tmp_path / "movie.mp4"
    path.write_bytes(b"")
    window._open_video(path)
    yield window
    # 動画モードは自動保存の対象外のため、未保存のまま close() すると
    # 破棄確認のモーダルダイアログでテストが止まる
    window._dirty = False


def _rect_region() -> Region:
    return Region(kind=RegionKind.RECT, rect=QRectF(0, 0, 10, 10))


class TestTimelineWindowIntegration:
    def test_open_video_shows_timeline_window(self, video):
        assert video._timeline_window is not None
        assert video._timeline_window.isVisible()

    def test_leave_video_hides_timeline_window(self, video):
        video._leave_video_mode()
        assert not video._timeline_window.isVisible()

    def test_timeline_action_enabled_only_in_video_mode(self, window, video):
        assert video._timeline_act.isEnabled()
        video._leave_video_mode()
        assert not video._timeline_act.isEnabled()

    def test_timeline_action_reopens_the_window(self, video):
        video._timeline_window.close()
        video._timeline_act.trigger()
        assert video._timeline_window.isVisible()

    def test_seek_from_window_moves_bottom_bar(self, video):
        video._timeline_window.seek_requested.emit(30)
        assert video._timeline.frame() == 30
        assert video._video.frame == 30

    def test_frame_change_moves_playhead(self, video):
        video._timeline.seek(20)
        assert video._timeline_window._area._frame == 20

    def test_interval_edit_marks_dirty(self, video):
        region = _rect_region()
        video.canvas.add_region(region)      # sync で現在フレームの区間になる
        video._dirty = False
        vr = video._video.find(region)
        video._timeline_window.interval_edited.emit(region, 0, 50)
        assert (vr.start, vr.end) == (0, 50)
        assert video._dirty

    def test_delete_from_window_removes_region(self, video):
        region = _rect_region()
        video.canvas.add_region(region)
        video._timeline_window.delete_requested.emit(region)
        assert video._video.find(region) is None
        assert video.canvas.get_regions() == []

    def test_delete_offscreen_region_removes_from_session(self, video):
        # 現在フレーム(0)に掛からない範囲はキャンバスに無くても消せる
        region = _rect_region()
        video._video.regions.append(VideoRegion(region, 50, 60))
        video._timeline_window.delete_requested.emit(region)
        assert video._video.find(region) is None

    def test_region_click_seeks_to_clicked_frame_and_selects(self, video):
        region = _rect_region()
        video.canvas.add_region(region)
        video._video.find(region).end = 50
        video._timeline.seek(0)
        video._timeline_window.region_clicked.emit(region, 30)
        assert video._video.frame == 30
        assert video._timeline.frame() == 30
        assert video.canvas.selected_regions() == [region]

    def test_region_click_without_move_keeps_frame(self, video):
        region = _rect_region()
        video.canvas.add_region(region)
        video._timeline_window.region_clicked.emit(region, 0)
        assert video._video.frame == 0
        assert video.canvas.selected_regions() == [region]

    def test_selection_dropped_outside_interval(self, video):
        # 区間外のフレームへ移動したら選択は解除され、範囲も表示しない
        region = _rect_region()
        video.canvas.add_region(region)      # 区間はフレーム 0 のみ
        video.canvas.select_regions([region])
        video._timeline.seek(10)
        assert video.canvas.get_regions() == []
        assert video.canvas.selected_regions() == []
        # 区間そのものは残るので、戻れば再び表示される
        assert video._video.find(region) is not None
        video._timeline.seek(0)
        assert video.canvas.get_regions() == [region]

    def test_window_shows_all_intervals(self, video):
        video.canvas.add_region(_rect_region())
        assert len(video._timeline_window._area._rows) == 1


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
        # -ss は小数 6 桁で書き出すため、絶対誤差で比べる
        assert float(cmd[cmd.index("-ss") + 1]) == pytest.approx(9.5 / 30.0, abs=1e-6)
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
        # 最後の 1 枚として扱い、その場で区間のマージまで進める
        state.files = [Path(f"f{i}.jpg") for i in range(3)]
        state.idx = 2
        video._on_video_frame_detected(
            [{"bbox": [0, 0, 10, 10]}]
        )
        # 開始 10 + 2 枚目 × 間隔 5 = フレーム 20
        assert video._video.regions[0].start == 20
        video._cleanup_video_detect()

    def test_missing_ffmpeg_is_reported_before_extraction(self, video, monkeypatch):
        """ffmpeg が消えていたら起動を試みずに知らせること

        起動できなかった QProcess は finished を出さないため、そのまま走らせると
        「モデルを読み込み中...」のまま固まる。
        """
        monkeypatch.setattr(
            "mosaic_tool.app.video_ffmpeg.is_ffmpeg_ready", lambda: False
        )
        monkeypatch.setattr(
            "mosaic_tool.app.QProcess.start", lambda *a: pytest.fail("展開が始まった")
        )
        shown = {}
        monkeypatch.setattr(
            QMessageBox, "critical", lambda *a, **k: shown.setdefault("text", a[2])
        )
        video._start_video_detect({"m.pt": {"conf": 0.5, "classes": []}})
        assert video._video_detect is None
        assert "ffmpeg" in shown["text"]

    def test_extraction_that_fails_to_start_is_reported(self, video, captured, monkeypatch):
        """起動失敗では finished が来ないため errorOccurred で畳むこと"""
        shown = {}
        monkeypatch.setattr(
            QMessageBox, "critical", lambda *a, **k: shown.setdefault("text", a[2])
        )
        video._start_video_detect({"m.pt": {"conf": 0.5, "classes": []}})
        video._video_detect.proc.errorOccurred.emit(
            QProcess.ProcessError.FailedToStart
        )
        assert video._video_detect is None
        assert "開始できませんでした" in shown["text"]

    def test_crashed_extraction_is_not_reported_as_a_start_failure(
        self, video, captured, monkeypatch
    ):
        """起動後の異常は「開始できなかった」ではないため文言を分ける"""
        shown = {}
        monkeypatch.setattr(
            QMessageBox, "critical", lambda *a, **k: shown.setdefault("text", a[2])
        )
        video._start_video_detect({"m.pt": {"conf": 0.5, "classes": []}})
        video._video_detect.proc.errorOccurred.emit(QProcess.ProcessError.Crashed)
        assert video._video_detect is None
        assert "開始できませんでした" not in shown["text"]

    def test_intervals_are_clamped_to_the_range_end(self, video, captured):
        captured["result"] = (10, 22, 5)
        video._start_video_detect({"m.pt": {"conf": 0.5, "classes": []}})
        state = video._video_detect
        state.files = [Path(f"f{i}.jpg") for i in range(3)]
        state.idx = 2  # フレーム 20。間隔 5 なら本来 24 まで伸びる
        video._on_video_frame_detected([{"bbox": [0, 0, 10, 10]}])
        assert video._video.regions[0].end == 22
        video._cleanup_video_detect()


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
