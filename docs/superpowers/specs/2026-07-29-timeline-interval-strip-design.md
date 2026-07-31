# タイムライン区間バー UI 設計

日付: 2026-07-29
状態: 承認済み

## 目的

動画モードの区間設定が「開始←現在」「終了←現在」ボタンだけでは分かりにくい。
区間を視覚化し、直接ドラッグで調整できるようにする。

## 決定事項

- タイムラインのスライダー直上に区間バー(`IntervalStrip`)を追加する
- 全範囲の区間を薄い帯で表示し、選択中の範囲の区間だけ濃い帯 + 両端ハンドルにする
- 選択中の帯の両端をドラッグして開始/終了フレームを変更する
  (反対側の端と動画範囲でクランプ。ドラッグ中はラベルをリアルタイム更新)
- 薄い帯をクリックするとその範囲を選択する
  (現在フレームに掛からない範囲はゴースト表示機構で表示して選択)
- 「開始←現在」「終了←現在」ボタンは廃止する

## 構成

- `mosaic_tool/video/timeline.py`
  - `IntervalStrip(QWidget)`: フレーム↔X の線形マッピングで帯を描画。
    シグナル `interval_edited(start, end)`(選択中の区間の変更を逐次通知)、
    `interval_clicked(index)`(薄い帯のクリック)
  - `TimelineBar`: スライダーの上に strip を配置(QVBoxLayout)。
    `set_intervals(intervals, selected_index)` で表示を更新。
    `set_start_requested` / `set_end_requested` と関連ボタンを削除
- `mosaic_tool/app.py`
  - 範囲/選択/フレーム変化時に `set_intervals` へ全区間と選択 index を渡す
  - `interval_edited` → 選択中の VideoRegion の start/end を更新し dirty
  - `interval_clicked` → 該当範囲をキャンバスで選択(未表示ならゴースト表示)
  - `_set_interval_edge` を削除

## テスト

- マッピング(フレーム↔X)と端ドラッグのクランプ
- クリックのヒット判定と interval_clicked
- app 連携(ドラッグ相当の interval_edited でセッションが更新される)
- README の操作説明を更新
