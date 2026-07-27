# 開発者向けドキュメント

## ソースから起動

```
pip install -r requirements.txt
python -m mosaic_tool <画像ファイルまたはフォルダ>
```

`just run <画像ファイルまたはフォルダ>` でも同じです。

## 実行ファイルのビルド

```
powershell -ExecutionPolicy Bypass -File scripts/build.ps1
```

`dist\MosaicTool.exe` が生成されます(PyInstaller は自動でインストールされます)。

| オプション | 内容 |
|---|---|
| `-Python <path>` | 使用する Python を指定(既定: `.venv` → `py -3` → `python` の順に探索) |
| `-OneDir` | 1 ファイルではなくフォルダ形式で出力(起動が速い) |
| `-Clean` | `build/` `dist/` を削除してからビルド |

アイコンは `assets/icon.ico`（マスターは `assets/icon.png`、`python scripts/icon_assets.py` で再生成）が埋め込まれます。
アプリ名とバージョンは `mosaic_tool/version.py` で管理し、ウィンドウタイトルとビルド成果物名の双方に反映されます。

## 配布用 zip のパッケージング

```
just package -Clean
```

`just` を使わない場合は `powershell -ExecutionPolicy Bypass -File scripts/package.ps1 -Clean` と同じです。
`scripts/build.ps1` を onefile 形式で呼び出したうえで、以下の zip を `dist/` に生成します。

```
MosaicTool-v<バージョン>-win-x64.zip
└── MosaicTool-v<バージョン>-win-x64/
    ├── MosaicTool.exe
    └── README.md
```

`-Python` と `-Clean` は `scripts/build.ps1` へそのまま渡されます。配布物名は `scripts/package.ps1` が `mosaic_tool/version.py` から組み立てるため、命名規則を変えるときはこのスクリプトだけを直します。

## リリース

`.github/workflows/release.yml` が Windows ランナー上で `scripts/package.ps1` を実行します。

| きっかけ | 動作 |
|---|---|
| `v*` タグの push | zip をビルドし、そのタグの Release を作成して `MosaicTool-v<バージョン>-win-x64.zip` をアップロード |
| Actions からの手動実行 | zip をビルドし、Actions の成果物として保存(Release は作らない) |

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
| `powershell -ExecutionPolicy Bypass -File scripts/bump.ps1 patch` | `mosaic_tool/version.py` の `__version__` を更新して `chore(release): v1.0.1 にバージョンを更新` をコミット(引数は `patch` / `minor` / `major` / `x.y.z`) |
| `powershell -ExecutionPolicy Bypass -File scripts/tag.ps1` | `mosaic_tool/version.py` のバージョンで注釈付きタグ `v1.0.1` を作成し、HEAD とタグを push（`-Remote` で push 先、`-Branch` でリリース元として許可するブランチを変更可） |

いずれも実行前に未コミットの変更・バージョン形式・タグの重複・リリース元ブランチ（既定 `main`）を検査し、問題があれば何もせず中断します。

`just --list` で全タスクを確認できます（`just version` / `just run` / `just build` / `just package` / `just bump` / `just tag` / `just release`）。

## リポジトリ構成

| 場所 | 内容 |
|---|---|
| `mosaic_tool/` | アプリ本体（`__main__.py` がエントリポイント） |
| `assets/` | アイコン（PNG マスター / ICO）とサンプル画像 |
| `scripts/` | ビルド・パッケージング・リリース用の PowerShell スクリプトと、アイコン資産を生成する `icon_assets.py` |
| `tests/` | pytest のテスト |
| `docs/` | 設計メモ・計画・開発者向けドキュメント |
