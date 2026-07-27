# zip 配布への移行と README 再編 — 設計

## 目的

Release の配布物を exe 単体から zip に変更し、利用者向け README を同梱する。あわせて README.md を利用者向けに再編し、開発者向けの手順は `docs/development.md` へ移す。

## 成果物の形

```
MosaicTool-v<バージョン>-win-x64.zip
└── MosaicTool-v<バージョン>-win-x64/
    ├── MosaicTool.exe
    └── README.md
```

- exe は現行どおり PyInstaller の onefile 出力を使う（`-OneDir` は配布対象外）。
- zip 内にトップレベルフォルダを 1 つ置き、展開時に中身が散らばらないようにする。
- Release にアップロードするアセットは zip のみ。exe 単体は上げない。

## 1. パッケージングスクリプト

新規 `scripts/package.ps1` を追加する。`scripts/build.ps1` は「exe を作る」責務のまま変更しない。

責務:

1. `mosaic_tool/version.py` からアプリ名とバージョンを取得し、成果物名 `<APP_NAME>-v<バージョン>-win-x64` を組み立てる。
2. `scripts/build.ps1` を onefile 固定で呼ぶ。`-Python` / `-Clean` は透過的に渡す。
3. ステージングディレクトリ `build/package/<成果物名>/` を作り直し、`dist/<APP_NAME>.exe` と リポジトリ直下の `README.md` をコピーする。
4. `Compress-Archive` で `dist/<成果物名>.zip` を出力する（既存 zip は上書き）。
5. 出力パスを表示して終了する。

エラー処理:

- `README.md` が存在しなければ throw して中断する。
- exe が存在しない場合は `build.ps1` 側が throw する（既存挙動）。
- `$ErrorActionPreference = "Stop"` と `$repoRoot` 基準の実行は `build.ps1` と同じ方式に揃える。

**zip 名の決定は package.ps1 に一本化する。** 現在 `release.yml` が持っている `asset_name` の組み立てとリネーム処理は削除し、命名規則の定義箇所を 1 つにする。

justfile に `package` レシピを追加する（例: `just package -Clean`）。既存レシピと同じく `_ps` 経由で `-File` 呼び出しにする。

## 2. release.yml の変更

| 変更 | 内容 |
|---|---|
| ビルドステップ | `scripts/build.ps1` の呼び出しを `scripts/package.ps1 -Python python -Clean` に置き換える |
| 「成果物をリネーム」ステップ | 削除する（package.ps1 が正式名で出力するため不要） |
| `meta` ステップ | `asset_name` の出力を削除。`app_name` / `app_version` の取得と、タグと `mosaic_tool/version.py` の整合チェックは残す |
| `upload-artifact` | `path: dist/*.zip`、`name` は `${app_name}-v${app_version}-win-x64`（表示名のみ）、`if-no-files-found: error` は維持 |
| `action-gh-release` | `files: dist/*.zip`。commit SHA での固定と `generate_release_notes: true` は維持 |

トリガー条件（`v*` タグ push / 手動実行）と権限設定は変更しない。

## 3. ドキュメント再編

### README.md（利用者向けに書き換え）

構成:

1. 概要（現行の 1 行説明）
2. 導入 — Releases から zip をダウンロードし、展開して `MosaicTool.exe` を実行する。ウィンドウへのドラッグ&ドロップで画像・フォルダを開ける旨も記載（現行の説明を流用）
3. 操作（現行の表をそのまま）
4. 保存先（現行のまま）
5. 設定の保存（現行のまま）
6. 末尾に「開発者向けの手順は `docs/development.md`」のリンク 1 行

現行 README の「起動」内の `pip install` 手順、「実行ファイルのビルド」「リリース」「リポジトリ構成」は README から削除する。

### docs/development.md（新規）

現行 README から移設する内容:

1. ソースから起動（`pip install -r requirements.txt` / `python -m mosaic_tool`）
2. exe のビルド（`scripts/build.ps1` とオプション表、アイコンとバージョン管理の説明）
3. zip のパッケージング（`just package` / `scripts/package.ps1`、成果物の構成）— 新規節
4. リリース手順（`just release` 系、`bump.ps1` / `tag.ps1` の説明、タグとバージョンの整合要件）
5. リポジトリ構成の表

## 検証

PowerShell スクリプトのため pytest 対象外。ローカルで以下を確認する。

1. `just package -Clean` が成功し、`dist/MosaicTool-v<バージョン>-win-x64.zip` が生成される。
2. zip の中身が `MosaicTool-v<バージョン>-win-x64/` 配下の `MosaicTool.exe` と `README.md` の 2 ファイルのみである。
3. 展開した exe が起動する。
4. `README.md` を一時的に退避した状態で `package.ps1` を実行すると、日本語のエラーメッセージで中断する。

## スコープ外

- コード署名 / SmartScreen 対策
- インストーラ形式（MSI / Inno Setup）
- macOS / Linux 向けビルド
- `-OneDir` 形式の配布
