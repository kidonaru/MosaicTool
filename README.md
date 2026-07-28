# MosaicTool

画像の指定範囲にモザイクをかけるツール。

https://github.com/user-attachments/assets/b727d54e-397f-4e7a-adb6-37318c6c51df

## 導入

[Releases](https://github.com/kidonaru/MosaicTool/releases) から `MosaicTool-v<バージョン>-win-x64.zip` をダウンロードし、展開して `MosaicTool.exe` を実行します。インストールは不要です。

画像ファイルまたはフォルダは、`MosaicTool.exe` へのドラッグ&ドロップ、コマンドライン引数、ウィンドウ(画像の上を含む)へのドラッグ&ドロップのいずれでも開けます。編集中に画像ファイルをドロップすると編集リストの末尾へ追加され、フォルダをドロップした場合は開き直します。

## 操作

| 操作 | 方法 |
|---|---|
| ズーム | ホイール |
| スクロール | 右ドラッグ / 中ボタンドラッグ |
| 自由範囲 | 「ペン」モードで空き領域をドラッグ(太さ指定可) |
| 矩形範囲 | 「矩形」モードで空き領域をドラッグ |
| 移動 | 範囲をドラッグ(どのモードでも可) |
| 拡大縮小・変形 | 選択して四隅/各辺中央のハンドルをドラッグ(Shift で縦横比を保持) |
| 回転 | 選択して上部の丸ハンドルをドラッグ |
| 削除 | 選択して Delete / BackSpace |
| 取り消し | Ctrl+Z |
| 保存 | Ctrl+S |
| 自動検出 | 「自動検出」ボタン / D キー(自動検出ウィンドウが開く) |

### ショートカットキー

| キー | 動作 |
|---|---|
| 1 | ペンモードへ切替 |
| 2 | 矩形モードへ切替 |
| ← | 前の画像へ |
| → | 次の画像へ |
| Tab | プレビュー(範囲のアウトラインを隠す)の ON/OFF |
| Delete / BackSpace | 選択中の範囲を削除 |
| Ctrl+Z | 取り消し |
| Ctrl+S | 保存 |
| D | 自動検出ウィンドウを開く |

Ctrl+S 以外は、キャンバス(画像表示部)にフォーカスがある間だけ有効です。ツールバーの数値入力中はその入力が優先されます。

## 保存先

- ファイル単体: 同じ場所に `名前_mc.拡張子`
- フォルダ: 隣に `フォルダ名_mc\` を作成し 1 枚ごとに保存(保存後、自動で次の画像へ)

モザイクサイズは 5〜100px(5px 刻み)、1 枚の画像内で共通です。

## 自動検出 (任意)

YOLO 形式の検出モデルで、モザイク範囲を自動で追加できます。

1. ツールバーの「自動検出」(または `D` キー) を押すと自動検出ウィンドウが開きます
2. 初回は「セットアップ」を押します。推論用の実行環境 (CPU 版 約 250MB / GPU 版 約 2.5GB) と、
   標準の検出モデル 3 件 (顔・目・髪 / 合計 約 20MB) がダウンロードされます。
   `MosaicTool.exe` と同じ場所の `runtime` / `models` フォルダに入ります
3. 使うモデルにチェックを入れ、モデルごとに信頼度 (%) を決めて「検出実行」を押します

信頼度を下げると検出されやすくなります。適切な値はモデルによって違うため、
一覧の初期値は検証済みの推奨値です (根拠は [docs/detection-models.md](https://github.com/kidonaru/MosaicTool/blob/main/docs/detection-models.md))。

検出された範囲は通常の範囲と同じように移動・変形・削除でき、`Ctrl+Z` 一回でまとめて取り消せます。
既に引いてある範囲は消えません。

### 標準モデル

[Anzhc/Anzhcs_YOLOs](https://huggingface.co/Anzhc/Anzhcs_YOLOs) (AGPL-3.0) のセグメンテーションモデルです。

| モデル | 対象 | サイズ |
|---|---|---|
| `Anzhc Face seg 640 v4 y11n.pt` | 顔 | 5.7MB |
| `Anzhc Eyes -seg-hd.pt` | 目 | 6.6MB |
| `Anzhc HeadHair seg y8n.pt` | 髪 | 6.5MB |

### その他の検出モデル

以下は自動ダウンロードの対象外です。手動でダウンロードして `models` フォルダへ置き、
自動検出ウィンドウの「更新」を押すと一覧に現れます。

| モデル | 対象 | 入手先 | 推奨信頼度 |
|---|---|---|---|
| `cockAndBallDetection2D_v20.pt` | penis (2D) | [Civitai 310687](https://civitai.com/models/310687) (要ログイン) | 20% |
| `nsfw-seg-vagina-s.pt` | vagina | [NSFW-API/NSFW_Segmentation](https://huggingface.co/NSFW-API/NSFW_Segmentation) | 15% |
| `Anzhc Breasts Seg v1 1024s.pt` | 胸 | [Anzhc/Anzhcs_YOLOs](https://huggingface.co/Anzhc/Anzhcs_YOLOs) | 25% |

`task=segment` のモデルは多角形の範囲になり、`task=detect` のモデルは矩形になります。
モデルのライセンスはそれぞれの配布元に従います (MosaicTool 本体は MIT ですが、
モデルと推論環境は同梱せず実行時に取得するため本体には影響しません)。

バージョンを更新するときは、旧フォルダの `runtime` と `models` を新しい展開先へコピーすると、
実行環境とモデルを再ダウンロードせずに済みます。

## 設定の保存

以下はアプリ設定として保存され、次回起動時に復元されます(Windows ではレジストリ `HKEY_CURRENT_USER\Software\MosaicTool\MosaicTool`)。

変更の都度、即時保存されるもの:

- モザイクサイズ
- ペン太さ
- 自動保存の ON/OFF
- ツールモード(ペン / 矩形、既定はペン)
- 検出モデルごとの ON/OFF と信頼度

終了時に保存されるもの:

- ウィンドウの位置とサイズ

## 開発者向け

ソースからの起動・ビルド・パッケージング・リリース手順は [docs/development.md](https://github.com/kidonaru/MosaicTool/blob/main/docs/development.md) を参照してください。
