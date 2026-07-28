# 自動検出モデルの検証レポート

作成日: 2026-07-28

`models\` に置く検出モデルを実データで比較した記録。どのモデルなら多角形(POLYGON)範囲が
出るのか、どの信頼度しきい値が妥当かをまとめる。

## 検証環境

| 項目 | 内容 |
|---|---|
| アプリ | `dist\MosaicTool-v1.0.0-win-x64`(PyInstaller onefile) |
| 推論環境 | 同梱 uv が構築した `runtime\` の venv(ultralytics) |
| 実行方法 | `detect_worker.py` を直接起動し、標準入出力の JSON で検証 |
| テスト画像 | `C:\tools\automosaic_2025-06-24_3\input` の 29 枚(AI 生成のイラスト系) |

## 発端: bbox しか出ない原因

当初 `models\` に置かれていた 3 つはいずれも**検出専用モデル**だった。

| モデル | task | クラス |
|---|---|---|
| `best.pt` | detect | penis |
| `pussyV2.pt` | detect | pussy |
| `yolov8x6_animeface.pt` | detect | face |

`task=detect` のモデルはセグメンテーションヘッドを持たないため `results[0].masks` が `None` になり、
`convert.py` が設計どおり bbox の矩形範囲へフォールバックしていた。実装の不具合ではない。

参考にした automosaic も同じで、`automosaic.py:93-96` でマスクが無ければ `create_mask_from_bbox()`
により bbox を矩形マスクへ変換している。つまり automosaic でこれらのモデルを使ってもモザイクの形は矩形。

多角形を出すには `task=segment` のモデルが必要。

## パイプラインの疎通確認

`yolov8n-seg.pt`(ultralytics 公式 COCO、7MB)を `bus.jpg` に適用し、多角形が最後まで通ることを確認した。

| ワーカー出力 | 間引き後の Region |
|---|---|
| 253 点 | 93 点 |
| 383 点 | 161 点 |
| 208 点 | 90 点 |
| 697 点 | 293 点 |
| 139 点 | 57 点 |
| 94 点 | 41 点 |

6 件すべて `RegionKind.POLYGON` へ変換され、bbox フォールバックは発生しなかった。
間引き(`convert.py` の `POLYGON_SIMPLIFY_RATIO = 0.004`)により点数は約 40% に減る。

## 採用したモデル

いずれも `task=segment`。

| ファイル | クラス | サイズ | 入手先 |
|---|---|---|---|
| `Anzhc Eyes -seg-hd.pt` | Eyes | 6MB | [Anzhc/Anzhcs_YOLOs](https://huggingface.co/Anzhc/Anzhcs_YOLOs) (AGPL-3.0) |
| `Anzhc Face seg 640 v4 y11n.pt` | face | 5MB | 同上 |
| `cockAndBallDetection2D_v20.pt` | penis | 119MB | [Civitai 310687](https://civitai.com/models/310687)(要ログイン) |
| `nsfw-seg-vagina-s.pt` | item | 19MB | [NSFW-API/NSFW_Segmentation](https://huggingface.co/NSFW-API/NSFW_Segmentation) |

## 検証結果

### 目 (`Anzhc Eyes -seg-hd.pt`)

29 枚すべてに conf 0.25 で適用。

- **28/29 枚で検出**、計 64 件、全件が多角形(7〜450 点)
- 左右の目が別インスタンスとして出るため、1 枚あたり 2 件が基本
- 主要な検出は conf 0.77〜0.89 と安定。conf 0.28〜0.30 の低スコア品が混ざる

### 顔 (`Anzhc Face seg 640 v4 y11n.pt`)

- 全枚数で安定検出。conf 0.88〜0.90、154〜373 点
- 低スコアの誤検出あり(conf 0.53 で 8 点、conf 0.16 で 11 点)。点数が極端に少ないものは誤検出の可能性が高い

### penis — ドメイン不一致の実例

`penis.pt`(automosaic 付属の検出専用モデル)が検出した 11 枚を陽性サンプルとして比較した。

| モデル | 検出率 | 備考 |
|---|---|---|
| `penis.pt`(検出専用) | **11/11** @ conf 0.25 | 矩形のみ |
| `cockAndBallDetection2D_v20.pt`(2D 学習) | **8/11** | うち 6 枚は conf 0.85〜0.94、27〜479 点 |
| `nsfw-seg-penis-x.pt`(実写向け) | 4/11 @ conf **0.05** | 実質使えない |
| `nsfw-seg-penis-s.pt`(実写向け) | 3/11 @ conf **0.05** | 同上 |

NSFW-API の README は Mask mAP@0.5 = 0.995 と謳うが、これは実写ドメインでの数値。
テスト画像はイラスト系のため一致せず、しきい値を 0.05 まで下げても取れなかった。
**モデル選定ではドメイン(実写 / 2D)の一致が精度指標より効く。**

2D 版に替えると当たる画像では conf 0.85〜0.94 まで上がる。ただし 3 枚は conf 0.05 でも検出されない。
取りこぼしを許容できない場合は `penis.pt` を併用できるが、当たった箇所は矩形と多角形が二重に付く。

### その他

- `nsfw-seg-vagina-s.pt`: 検出はするが conf 0.19〜0.22 と低い
- `nsfw-seg-breast-s.pt`: 6 枚中 2 枚で検出(conf 0.32〜0.89)。現在は `models\` から外している

## 信頼度しきい値の目安

モデルごとに最適値がばらつく。現在の実装は全モデル共通の値を 1 つしか持てないため、
妥協点は **20% 前後**。

| 対象 | 目安 | 理由 |
|---|---|---|
| 目 | 40% 以上 | 低スコアの誤検出を除ける |
| 顔 | 25% | 既定値で安定 |
| penis (2D) | 20% | conf 0.23〜0.28 の正解が混じる |
| vagina | 15% | そもそも 0.19〜0.22 でしか出ない |

## 入手先について

- **HuggingFace**: 認証不要で直接ダウンロードできる。ただし検索結果は LoRA が大半で、
  実用的な検出モデルは Anzhc と NSFW-API のものに限られる
- **Civitai**: 2D / イラスト向けの ADetailer 用モデルが充実しているが、
  ダウンロード URL は認証必須(未ログインだと `auth.civitai.com/login` へリダイレクトされる)。
  API キーを使うかブラウザから手動で取得する

2D 向けの候補(いずれも Civitai):

- [Cock and Ball Detection 2D edition](https://civitai.com/models/310687) — YOLOv11x seg、採用済み
- [ADetailer Vagina / Pussy Model](https://civitai.com/models/150872) — seg 版あり
- [assDetailer](https://civitai.com/models/1156687) — v2-segm
- [Eye Detailer/Segmentation](https://civitai.com/models/334668) — 目の seg

## ライセンス

モデルごとに異なり、Anzhc は AGPL-3.0、NSFW-API は表記なし、Civitai のものは各ページの規約に従う。

MosaicTool 本体は MIT だが、**モデルを同梱せずユーザーが用意する設計**のため本体への影響はない。
同じ理由で ultralytics(AGPL-3.0)も別プロセスの venv に隔離されている。

## 今後の検討事項

- **検出元モデルの表示**: 現在 `Region` はどのモデル由来かを持たないため、UI から判別できない。
  不要なモデルを外したいときの手がかりが無い(モデル単位の ON/OFF は自動検出ウィンドウで行える)
- **髪モデルの信頼度**: `Anzhc HeadHair seg y8n.pt` は標準モデルに加えたが、
  この検証には含めていない。推奨値は暫定で 25%
