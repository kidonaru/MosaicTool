# 自動モザイク範囲検出 設計

## 目的

YOLO 系の検出モデルでセンシティブな部位を自動検出し、その結果を MosaicTool の編集可能な範囲(`Region`)として追加する。ユーザーは自動で置かれた範囲を確認・手直ししてから保存する。

参考実装: `C:\tools\automosaic_2025-06-24_3\automosaic.py`(ultralytics + Pillow の CLI ツール)。

## 前提と制約

MosaicTool は「展開して exe を実行するだけ」の軽量ポータブルアプリであり、本体は PySide6 + Pillow のみに依存する。ultralytics / torch は数百 MB〜2.5GB あり、本体 exe に同梱するとこの性質が失われる。

したがって推論環境は**オプション拡張**として、ユーザーの操作を起点に後から構築する。検出モデル(`.pt`)は第三者製で再配布ライセンスが不明なため同梱せず、ユーザーが用意する。

## アーキテクチャ

本体 exe は ultralytics / torch を一切 import しない。推論は別プロセスの venv 側だけで動き、両者は JSON で会話する。

```
MosaicTool.exe (PySide6, frozen)
  │
  ├─ models\        ユーザーが .pt を置く
  ├─ runtime\       uv が作る venv (ultralytics, torch)
  └─ uv.exe         同梱 (+15MB)
       │
       └─ runtime\Scripts\python.exe worker_main.py   ← 常駐ワーカー
                    stdin  ← {"image": "...", "conf": 0.25}
                    stdout → {"ok": true, "detections": [...]}
```

`models\` と `runtime\` はいずれも exe と同じディレクトリに置く(ソース実行時はリポジトリルート)。PyInstaller の frozen 判定で `sys.executable` の親を見る。フォルダごと持ち運べるポータブル性を優先した結果であり、バージョン更新時は新しい展開先へ `runtime\` と `models\` をコピーすれば再構築を避けられる。

推論プロセスは**常駐**させる。モデル読み込みに 3〜10 秒かかるため、都度起動では連続作業が成立しない。初回の検出実行時に起動し、以降は標準入出力で画像パスを渡す。アプリ終了時に終了させる。

### モジュール構成

| ファイル | 役割 |
|---|---|
| `mosaic_tool/detect/runtime.py` (新規) | `uv` を叩いて `runtime\` に venv を構築。GPU/CPU の出し分け。QProcess で非同期・進捗をシグナル通知 |
| `mosaic_tool/detect/worker_client.py` (新規) | ワーカーの起動・常駐管理・JSON 送受信。異常終了したら次回黙って再起動 |
| `mosaic_tool/detect/convert.py` (新規) | 検出結果 JSON → `list[Region]`。GUI 非依存の純粋関数群 |
| `mosaic_tool/detect/worker_main.py` (新規) | venv 側で動くスクリプト。ultralytics を import する唯一の場所 |
| `mosaic_tool/regions.py` | `RegionKind.POLYGON` を追加 |
| `mosaic_tool/canvas.py` | 複数 Region の一括追加(undo 1 回分)、POLYGON 対応 |
| `mosaic_tool/app.py` | ツールバーに「自動検出」ボタン、セットアップ導線、進捗表示 |
| `mosaic_tool/settings.py` | 信頼度しきい値・デバイス設定の永続化 |

`worker_main.py` は frozen exe に含まれるが exe 内では実行されない。venv の Python にスクリプトパスとして渡す必要があるため、初回に `runtime\` へコピーする(アプリのバージョン文字列で更新を判定)。

境界を JSON に置いたことで、`convert.py` は ultralytics も Qt のイベントループも要らず単体テストできる。`worker_client.py` は QProcess を持つため Qt 依存だが、プロトコルの組み立て・解釈は `convert.py` 側に寄せる。

## POLYGON 範囲

セグメンテーションマスクの輪郭は「太い折れ線」では表現できないため、`Region` に新しい種別を追加する。

```python
class RegionKind(Enum):
    RECT = "rect"
    STROKE = "stroke"
    POLYGON = "polygon"   # points を閉じた多角形として扱う
```

既存の `points` フィールドを再利用し、`kind` で解釈を変える。`local_path()` に分岐を 1 つ足す:

```python
if self.kind is RegionKind.POLYGON:
    path.addPolygon(QPolygonF(self.points))
    path.closeSubpath()
    return path
```

`image_transform()` / `image_path()` は `local_path()` の上に乗っているため変更不要。`canvas.py` の `RegionItem` も描画・当たり判定は `local_path()` 経由、ハンドル位置は `boundingRect()` 基準なので、移動・拡大縮小・回転・削除・Ctrl+Z がそのまま効く。ペン太さ UI は STROKE にしか関係しないため変更しない。

範囲はメモリ上にしか持たない(プロジェクトファイルが無い)ため、永続化の互換性を考慮する必要はない。

## 検出結果の変換

ワーカーは検出ごとに `bbox` を返し、モデルがセグメンテーション対応なら `polygon` も返す。輪郭は ultralytics の `results[0].masks.xy` から画像座標の点列として直接得る(automosaic はマスク画像化とアスペクト比補正のリサイズを経ているが、その処理は踏襲しない)。

- `polygon` があれば → `POLYGON` Region
- 無ければ(検出専用モデル)→ `bbox` から `RECT` Region にフォールバック

`masks.xy` は輪郭を数百点で返すことがあり、そのまま保持するとハンドル操作のたびのパス再構築が重くなる。隣接点の距離が一定未満なら間引く簡易な削減を `convert.py` に置き、数十点程度へ落とす。しきい値は画像サイズに対する比率の定数として定義する。

`pos` / `rotation` / `scale` は初期値(未変形)、点列は画像座標をそのまま入れる。

### 追加の仕方

- 既存の範囲は消さずに追加する。ユーザーが手で引いた範囲は保持される。
- 追加した Region は全て選択状態にし、どれが自動追加分か一目で分かるようにする。
- `_undo_stack` に `("add_many", items)` を 1 エントリ積み、Ctrl+Z 一回で検出結果を丸ごと取り消せるようにする(`canvas.undo()` に分岐を 1 つ追加)。

## UI

ツールバー末尾に 2 つ追加する。

- `自動検出` ボタン(ショートカット `D`、キャンバスにフォーカスがある間のみ有効)
- `信頼度 25 %` スピンボックス(1〜100%、既定 25 = automosaic 準拠)

デバイス設定(自動 / CPU 固定)と信頼度は `AppSettings` に即時保存し、次回起動時に復元する。

### 初回セットアップ

1. `自動検出` 押下 → `runtime\` が無ければ確認ダイアログ
2. GPU/CPU を選択。`nvidia-smi` の有無で既定を出し分ける(「GPU を使う: 約2.5GB」/「CPU のみ: 約250MB」)
3. 進捗ダイアログ: `uv venv runtime --python 3.11` → `uv pip install --python runtime ultralytics torch torchvision`(GPU 時のみ `--extra-index-url` を付与)。出力をログ欄に流し、キャンセル可
4. 完了後そのまま検出を実行

`models\` に `.pt` が 1 つも無い場合は、セットアップ前に「`models` フォルダに検出モデルを置いてください」と案内し、フォルダを開くボタンを添える。

`models\` 内の `.pt` は全て読み込み、結果を合成する(automosaic と同じ挙動)。

### 検出実行

すべて `QProcess` の非同期処理で、UI は固まらない。実行中はボタンを無効化し、ステータスバーに「検出中...」を表示。完了時は「N 件の範囲を追加しました」、0 件なら「検出されませんでした」。保存処理は既存の `apply_mosaic` をそのまま通るため変更しない。

## エラー処理

| 状況 | 挙動 |
|---|---|
| セットアップ失敗(ネット断など) | ログ付きエラー表示。`runtime\` を削除して再試行できる状態に戻す |
| ワーカーの異常終了 | stderr の末尾をエラー表示。次回実行時に黙って再起動 |
| 検出のタイムアウト(既定 120 秒) | ワーカーを kill してエラー表示 |
| アプリ終了 | ワーカーを terminate(応答がなければ kill) |

## テスト

`ultralytics` を CI に導入しないため、JSON に置いた境界の内側だけを検証する。

- `convert.py`: JSON → Region(polygon / bbox フォールバック / 点間引き / 空結果 / 不正な JSON)
- `regions.py`: POLYGON の `local_path` / `image_path`(回転・拡大後の座標)
- `canvas.py`: 一括追加が undo 1 回で戻ること
- `runtime.py`: GPU/CPU で組み立てられる `uv` コマンド列が変わること(実行はしない)
- `worker_client.py`: QProcess をフェイクに差し替えてプロトコルの送受信を検証

実モデルを使った検出は自動テストの対象外とし、手動確認とする。

## 配布

`uv.exe` を PyInstaller の `datas` に追加する(+15MB)。README に「`models` フォルダにモデルを置く」「更新時は `runtime\` と `models\` を新しい展開先へコピーする」を追記する。

## スコープ外

- フォルダ内画像の一括自動検出
- 次画像の先読み検出
- モデルごとの ON/OFF 切り替え
- 多角形の頂点単位の編集
- モデルファイルの同梱・自動ダウンロード
