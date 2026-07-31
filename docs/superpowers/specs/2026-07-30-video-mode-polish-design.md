# 動画モードの操作性改善 設計

作成日: 2026-07-30

## 背景と目的

動画モードは「フレームを 1 枚ずつ表示して範囲を置き、タイムラインで区間を調整する」構成で動いている。
実運用で以下が不足している。

1. 自動検出が常に全編対象で、長い動画の一部だけを検出できない
2. タイムラインの横移動が Ctrl+ホイール（ズーム）とスクロールバーしかなく、素の横スクロールがない
3. タイムラインの縦ラインが約 80px 間隔のラベル位置にしかなく、区間の端をフレーム単位で読み取りにくい
4. 動きを確認する手段がなく、区間の当たり外れをコマ送りでしか確かめられない

本設計はこの 4 点を解消する。

## スコープ

対象は動画モードのみ。画像モードの挙動は変えない。
非対象: 音声再生、タイムラインへの波形表示、区間のコピー／貼り付け、検出条件のディスク永続化。

## 1. 検出範囲ダイアログ

### 新規モジュール `mosaic_tool/video/detect_range_dialog.py`

動画モードで「検出実行」を押したときに開くモーダルダイアログ `DetectRangeDialog`。

```
開始フレーム [    120 ]  00:04.00
終了フレーム [   1799 ]  00:59.97
検出間隔     [      3 ] フレーム
─────────────────────────────
約 560 フレームを検出します
                  [ OK ] [ キャンセル ]
```

- 既定値: 開始 = 現在フレーム、終了 = 最終フレーム、検出間隔 = 前回値（`MainWindow` がセッション中だけ保持、初回 1）
- 開始 > 終了にならないよう、開始の変更で終了の下限、終了の変更で開始の上限を更新する
- 各スピンの右にタイムコードを出し、値変更で更新する
- 件数ラベルも値変更で更新する。従来の `QMessageBox.question` による確認はこのダイアログが兼ねるため削除する
- 結果は `range_result() -> (start, end, step)` で取り出す

### 純関数（テスト対象）

- `format_timecode(frame: int, fps: float) -> str` — `MM:SS.ss` 形式。1 時間を超える動画は `H:MM:SS.ss`
- `detect_frame_count(start: int, end: int, step: int) -> int` — `(end - start) // step + 1`

### 下部バーからの移設

`TimelineBar` の「検出間隔」スピン（`detect_step()`）を削除し、ダイアログへ移す。
空いた領域に再生ボタンと速度コンボを置く（4 節）。

### ffmpeg コマンドの範囲対応

`ffmpeg.extract_frames_command(src, info, step, out_pattern)` に `start: int` と `count: int` を追加する。

- 既存の `extract_frame_command` と同じ「前フレームとの中間時刻へ `-ss`（入力前・高速シーク）」方式で開始位置へ飛ぶ
- `-vf fps=<expr>,select='not(mod(n\,step))'` と `-frames:v count` で必要枚数だけ書き出す
- シーク後の `n` は 0 から数え直されるため、k 枚目（0 始まり）は元動画のフレーム `start + k * step` に対応する

`app.py` の `_on_video_frame_detected` はフレーム番号を `state.start + state.idx * state.step` で求める。
`merge_detections` へ渡す `total_frames` は `end + 1` とし、区間の末尾伸長が指定範囲の外へ出ないようクランプする。

## 2. タイムラインのホイール操作

`TimelineArea.wheelEvent` を修飾キーで分岐する。

| 操作 | 挙動 |
| --- | --- |
| 修飾なし | 横スクロール |
| Ctrl | 横ズーム（カーソル下のフレームを固定。既存挙動） |
| Shift | 縦スクロール（`super()` へ受け流す） |

横スクロールは新シグナル `hscroll_requested(int)` で相対量を通知し、`TimelineWindow` が水平スクロールバーの値へ加算する。
ズーム時のアンカー補正に使う既存の `scroll_requested`（絶対値）とは別物として残す。

## 3. 縦ラインの細分化

表示フレーム数（ズームの範囲 `ZOOM_MIN`〜`ZOOM_MAX`）は変更しない。
`_tick_interval` が返す主目盛りを分割した副目盛りを追加する。

- `_minor_interval(major: int, px_per_frame: float) -> int` — 主目盛りを 2 / 5 / 10 で割った候補のうち、
  副目盛りの間隔が `MIN_MINOR_PX`（8px）以上に収まる最も細かいものを返す。割り切れない分割は使わない。
  分割できなければ主目盛りをそのまま返す（この場合は副目盛りを描かない）
- ルーラー: 主 = 長い目盛り＋フレーム番号、副 = 短い目盛り
- 行エリア: 主 = やや明るい縦線、副 = 暗い縦線を、行の全高にわたって描く

描画順は「行背景 → 縦線 → 区間バー」とし、バーが縦線に埋もれないようにする。
縦線の描画も既存のバーと同じく可視フレーム範囲だけに限る。

## 4. 再生モード

### 新規モジュール `mosaic_tool/video/player.py`

#### `FrameReader(QThread)`

再生開始フレームから ffmpeg を 1 本だけ起動し、フレームを連続で受け取る。

- コマンド: `-ss <(start-0.5)/fps>` → `-i src` → `-vf fps=<expr>,scale=<pw>:<ph>` → `-f rawvideo -pix_fmt rgb24 -`
- プロキシ幅は `PROXY_MAX_WIDTH`（960）を上限に、元の縦横比を保った偶数サイズへ丸める。
  元動画がこれより小さければ原寸のまま
- 1 フレームは `pw * ph * 3` バイト固定なので、フレーム境界の解析が不要
- 上限付きキュー（`QUEUE_SIZE` = 30 枚）へ入れる。満杯なら待つため、パイプの読み出しが止まり
  ffmpeg 側の流量が自然に絞られる
- `stop()` でプロセスを kill し、キューを空にしてスレッドを終える

#### `VideoPlayer(QObject)`

- `QTimer`（`TICK_MS` = 10ms）が壁時計の経過時間から目標フレーム `start + elapsed * fps * speed` を求める
- キューから目標フレームまで読み捨てて最新を取り出し、`frame_ready(index, QImage)` を発火する（実時間優先のコマ落ち）
- キューが枯れたらその分だけ時計の基準時刻をずらし、復帰時に大量のコマ落ちが起きないようにする
- 速度は 0.25 / 0.5 / 1 / 2 倍
- 終端（`index >= frame_count - 1`）または reader の終了で停止し `finished` を発火する
- 目標フレームの計算 `target_frame(start, elapsed, fps, speed)` は純関数として切り出しテストする

### キャンバス側の追加

`MosaicCanvas.set_playback_image(qimage: QImage) -> None`

`set_image` はシーンを作り直し `_fit_to_window` でズームを戻すため、毎フレーム呼べない。
`set_playback_image` はシーン構成・ズーム・範囲アイテムを保ったまま `_pixmap_item` の pixmap だけ差し替える。
プロキシ画像はシーン矩形（元動画の解像度）へ拡大して表示するので、範囲の座標はずれない。

表示内容は既存のプレビュートグル（Tab）の状態に従う。

- プレビュー OFF: 枠線つきのまま映像が動く。pixmap 差し替えのみ
- プレビュー ON: プロキシ解像度からモザイクを作る。ブロックサイズはプロキシ倍率で換算した近似値
  （`max(1, round(block * scale))`）を使い、生成した pixmap をシーン矩形へ拡大して重ねる。
  近似であることは仕様として受け入れる（正確な仕上がりは静止状態のプレビューと書き出しで確認する）

`MosaicCanvas.set_playback_mode(on: bool) -> None`

再生中は範囲の作成・選択・変形を止める。プレビューと違い枠線は描いたままにする。

クリップ形状（`_update_clip`）は `mask_to_cell_grid` を通すため重い。
再生中は「そのフレームに掛かる範囲の集合」が前フレームと変わったときだけ再計算する。

### `app.py` の統合

- `_playback: VideoPlayer | None` を持つ
- 開始: `_sync_video_regions()` → `canvas.set_playback_mode(True)` → 現在フレームから `VideoPlayer` を起動
- `frame_ready`: `video.frame` を更新し、`canvas.set_playback_image`、掛かる範囲の集合が変わっていれば
  範囲アイテムを差し替え、`TimelineBar.set_frame`（シグナルを出さない経路）と
  `TimelineWindow.set_frame` で再生ヘッドを進める
- 停止: プレイヤーを止め、`canvas.set_playback_mode(False)` の後 `_show_frame(現在フレーム)` で原寸フレームへ戻す
- 排他: `_reject_while_video_busy` に再生中を含め、検出・書き出し・ファイル切替・終了の前に停止させる

### 操作 UI

`TimelineBar` に追加する。

- ▶ / ⏸ トグルボタン（検出間隔スピンを外して空いた場所）。Space での二重発火を避けるため
  `setFocusPolicy(NoFocus)` を付ける
- 速度コンボ（0.25x / 0.5x / 1x / 2x、既定 1x）

Space での再生／一時停止は 2 経路で用意する。既存の修飾なしショートカットは
`_add_shortcut(canvas_only=True)` によりキャンバスにフォーカスがある間だけ効く仕組みで、
ツールバーのスピンボックスへの文字入力を奪わないためにこれは崩さない。

- キャンバス側: `_playback_act` を Space の `QAction`（`canvas_only=True`）として登録する
- タイムラインウィンドウ側: `TimelineArea.keyPressEvent` で Space を拾い、
  `playback_toggle_requested` を発火して `MainWindow` の同じハンドラへつなぐ

## テスト方針

既存の `tests/test_*.py` と同じ構成で追加する。GUI を起こさず済む単位に純関数を切り出してあるため、
以下はウィジェット生成なしで検証できる。

- `format_timecode` / `detect_frame_count`（境界: 0 フレーム、1 時間超、step が範囲より大きい）
- `extract_frames_command` の範囲指定（`-ss` の時刻、`select` の式、`-frames:v` の枚数）
- `_minor_interval`（分割できない主目盛り、`MIN_MINOR_PX` 直前後のズーム）
- `VideoPlayer` の `target_frame`（速度倍率、経過 0、終端クランプ）

ウィジェットを使うテスト（`DetectRangeDialog` の既定値と相互クランプ、`TimelineBar` の速度取得）は
既存の `tests/conftest.py` の QApplication フィクスチャに乗せる。

`FrameReader` は ffmpeg の実行を伴うため単体テストの対象外とし、パイプの読み出し（固定バイト数の
フレーム分割）だけを純関数化して検証する。

## 想定される影響範囲

| ファイル | 変更 |
| --- | --- |
| `mosaic_tool/video/detect_range_dialog.py` | 新規 |
| `mosaic_tool/video/player.py` | 新規 |
| `mosaic_tool/video/ffmpeg.py` | `extract_frames_command` に範囲指定を追加、再生用コマンドを追加 |
| `mosaic_tool/video/timeline.py` | 検出間隔スピンを削除、再生ボタンと速度コンボを追加 |
| `mosaic_tool/video/timeline_window.py` | ホイールの分岐、副目盛りと縦線の描画 |
| `mosaic_tool/canvas.py` | `set_playback_image` / `set_playback_mode`、クリップ再計算の間引き |
| `mosaic_tool/app.py` | 検出範囲ダイアログの呼び出し、範囲つき検出、再生の統合と排他 |
| `docs/` | 動画モードの操作説明へ再生と検出範囲を追記 |
