"""MainWindow のプレビュー操作(Tab ショートカット・画像切替時の解除)の検証"""
import io as std_io
import os
from pathlib import Path

import pytest
from PySide6.QtCore import QRectF, QSettings, Qt
from PySide6.QtGui import QKeySequence

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
from mosaic_tool.video.ffmpeg import VideoInfo  # noqa: E402
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
