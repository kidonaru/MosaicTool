# 開発者向けドキュメント

## ソースから起動

```
pip install -r requirements.txt
python -m mosaic_tool <画像ファイルまたはフォルダ>
```

`just run <画像ファイルまたはフォルダ>` でも同じです。

## 実行ファイルのビルド

```
python scripts/build.py
```

Windows では `dist/MosaicTool.exe` が、macOS では `dist/MosaicTool.app` が生成されます
(PyInstaller は自動でインストールされます)。

| オプション | 内容 |
|---|---|
| `--python <path>` | 使用する Python を指定(既定: 実行中の Python) |
| `--onedir` | 1 ファイルではなくフォルダ形式で出力(起動が速い。Windows のみ有効) |
| `--clean` | `build/` `dist/` を削除してからビルド |
| `--uv-version <ver>` | 同梱する uv のバージョン(既定: `latest`) |
| `--sign-identity <id>` | macOS の署名 ID(既定: 環境変数 `MACOS_SIGN_IDENTITY`) |

macOS は `.app` バンドルを作るため常に onedir で、`--onedir` の指定は不要です。

自動検出の実行環境セットアップに使う uv(約 50MB)を GitHub から取得して `build/uv/` に
キャッシュし、実行ファイルへ同梱します。あわせて検出ワーカー `mosaic_tool/detect/worker_main.py` を
`.py` の実体として同梱します(venv の Python へスクリプトのパスとして渡すため、
PYZ に取り込まれるだけでは足りません)。同梱物は `sys._MEIPASS` を基準に解決します。

アイコンのマスターは `assets/icon.png` で、`python scripts/icon_assets.py` を実行すると
`assets/icon.ico`(Windows / `QIcon`)と `assets/icon.icns`(macOS の `.app` バンドル)が
再生成されます。バンドルアイコンには OS ごとにこのどちらかが埋め込まれます。
アプリ名とバージョンは `mosaic_tool/version.py` で管理し、ウィンドウタイトルとビルド成果物名の双方に反映されます。

## 配布用 zip のパッケージング

```
just package --clean
```

`just` を使わない場合は `python scripts/package.py --clean` と同じです。
`scripts/build.py` を呼び出したうえで、以下の zip を `dist/` に生成します。

```
MosaicTool-v<バージョン>-win-x64.zip
└── MosaicTool-v<バージョン>-win-x64/
    ├── MosaicTool.exe
    └── README.md
```

```
MosaicTool-v<バージョン>-mac-arm64.zip
└── MosaicTool-v<バージョン>-mac-arm64/
    ├── MosaicTool.app
    └── README.md
```

macOS の zip は `ditto` で作ります(`zipfile` では `.app` の実行ビットとシンボリックリンクが
壊れ、展開したアプリが起動しなくなるため)。

`--python` と `--clean` は `scripts/build.py` へそのまま渡されます。配布物名は `scripts/package.py` が `mosaic_tool/version.py` から組み立てるため、命名規則を変えるときはこのスクリプトだけを直します。

## リリース

`.github/workflows/release.yml` が Windows と macOS の 2 ランナー上で `scripts/package.py` を
実行し、独立した `release` ジョブが両方の zip をまとめて Release へアップロードします。
先頭の `meta` ジョブがタグとバージョンの整合を検証するため、不一致なら 2 つのビルドは始まりません。

| きっかけ | 動作 |
|---|---|
| `v*` タグの push | 両 OS の zip をビルドし、そのタグの Release を作成して `MosaicTool-v<バージョン>-win-x64.zip` と `MosaicTool-v<バージョン>-mac-arm64.zip` をアップロード |
| Actions からの手動実行 | 両 OS の zip をビルドし、Actions の成果物として保存(Release は作らない) |

タグ名は `mosaic_tool/version.py` の `__version__` と一致していないとワークフローが失敗するため、リリース作業はスクリプト経由で行います。

```
just release patch    # 1.0.0 -> 1.0.1 にして、コミット・タグ・push まで実行
just release minor    # 1.0.0 -> 1.1.0
just release major    # 1.0.0 -> 2.0.0
just release 1.2.3    # バージョンを直接指定
```

引数を省略すると `patch` として扱われます(`just release` / `just bump`)。

`just` を使わない場合は以下と同じです。

| スクリプト | 内容 |
|---|---|
| `python scripts/bump.py patch` | `mosaic_tool/version.py` の `__version__` を更新して `chore(release): v1.0.1 にバージョンを更新` をコミット(引数は `patch` / `minor` / `major` / `x.y.z`) |
| `python scripts/tag.py` | `mosaic_tool/version.py` のバージョンで注釈付きタグ `v1.0.1` を作成し、HEAD とタグを push（`--remote` で push 先、`--branch` でリリース元として許可するブランチを変更可） |

いずれも実行前に未コミットの変更・バージョン形式・タグの重複・リリース元ブランチ（既定 `main`）を検査し、問題があれば何もせず中断します。

`just --list` で全タスクを確認できます（`just version` / `just run` / `just build` / `just package` / `just bump` / `just tag` / `just release`）。

## リポジトリ構成

| 場所 | 内容 |
|---|---|
| `mosaic_tool/` | アプリ本体（`__main__.py` がエントリポイント） |
| `assets/` | アイコン（PNG マスター / ICO / ICNS）とサンプル画像 |
| `scripts/` | ビルド・パッケージング・リリース用の Python スクリプト（アイコン資産を生成する `icon_assets.py` を含む） |
| `tests/` | pytest のテスト |
| `docs/` | 設計メモ・計画・開発者向けドキュメント |

## macOS の署名

`MosaicTool.app` の Developer ID 署名と公証の手順は
[docs/macos-signing.md](macos-signing.md) を参照してください。
Secrets が未設定の場合は ad-hoc 署名のままビルドが続行されます。
