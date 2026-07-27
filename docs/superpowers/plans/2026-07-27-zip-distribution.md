# zip 配布への移行と README 再編 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Release の配布物を exe 単体から「exe + 利用者向け README」の zip に変更し、README.md を利用者向けに再編して開発者向け手順を `docs/development.md` へ移す。

**Architecture:** 新規 `scripts/package.ps1` が既存の `scripts/build.ps1` を呼んで onefile exe を作り、ステージングディレクトリ経由で zip 化する。配布物の命名規則はこのスクリプトを唯一の情報源とし、`release.yml` からは名前の組み立てとリネーム処理を削除して `dist/*.zip` を glob で拾う形にする。

**Tech Stack:** PowerShell 5.1+ (Windows), PyInstaller, GitHub Actions, just

## Global Constraints

- 対象プラットフォームは Windows x64 のみ。
- コードコメントとエラーメッセージは日本語で書く。
- アプリ名とバージョンのハードコードは禁止。`mosaic_tool/version.py` を唯一の情報源とする。
- 配布物名は `<APP_NAME>-v<バージョン>-win-x64`（例: `MosaicTool-v1.0.0-win-x64`）。
- zip の中身は必ずトップレベルフォルダ 1 つ配下に置く。
- `mosaic_tool/version.py` の読み取りは既存 `scripts/tag.ps1` / `scripts/bump.ps1` と同じ方式に揃える（`[System.IO.File]::ReadAllText` + 正規表現。`import` はしない。CRLF 対策で行末を `$` で固定しない）。
- 既存 PowerShell スクリプトの規約に従う: `[CmdletBinding()]` + `param()`、`$ErrorActionPreference = "Stop"`、`$repoRoot = Split-Path -Parent $PSScriptRoot` して `Set-Location`、異常時は日本語メッセージで `throw`。
- リポジトリ URL: `https://github.com/kidonaru/MosaicTool`
- README.md は zip に同梱されるため、README 内のリポジトリ内リンクは相対パスではなく絶対 URL を使う。
- 本リポジトリの PowerShell スクリプトに自動テストは存在しない。検証は実際にコマンドを実行して出力を確認する。

---

## File Structure

| ファイル | 区分 | 責務 |
|---|---|---|
| `scripts/package.ps1` | 新規 | 配布用 zip の作成（命名規則の定義もここ） |
| `justfile` | 変更 | `package` レシピの追加 |
| `README.md` | 変更 | 利用者向けドキュメント（zip に同梱） |
| `docs/development.md` | 新規 | 開発者向けドキュメント（起動・ビルド・パッケージング・リリース・構成） |
| `.github/workflows/release.yml` | 変更 | zip をビルドして Release へアップロード |

`scripts/build.ps1` は変更しない（「exe を作る」責務のまま）。

---

### Task 1: 配布用 zip を作る package.ps1

**Files:**
- Create: `scripts/package.ps1`
- Modify: `justfile`（`build` レシピの直後に `package` レシピを追加）

**Interfaces:**
- Consumes: `scripts/build.ps1`（`-Python <path>` / `-OneDir` / `-Clean` を受け取り、`dist\<APP_NAME>.exe` を生成する）、`mosaic_tool/version.py`（`APP_NAME` と `__version__`）
- Produces: `dist/<APP_NAME>-v<バージョン>-win-x64.zip`。Task 3 の `release.yml` はこのパスを `dist/*.zip` で拾う。Task 2 の `docs/development.md` は `just package` / `scripts/package.ps1` の使い方を記述する。

- [ ] **Step 1: `scripts/package.ps1` を作成する**

```powershell
# MosaicTool の配布用 zip を作成する
# 使い方: powershell -ExecutionPolicy Bypass -File scripts/package.ps1
#   -Python <path>  : 使用する Python を指定 (build.ps1 へ透過)
#   -Clean          : build/ dist/ を削除してからビルド (build.ps1 へ透過)
[CmdletBinding()]
param(
    [string]$Python,
    [switch]$Clean
)

$ErrorActionPreference = "Stop"
# スクリプトは scripts/ に置くが、パッケージングはリポジトリ直下を基準に行う
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $repoRoot

# mosaic_tool/version.py を直接読む (import すると __pycache__ の古い .pyc を拾うことがある)
# .NET の API は Set-Location を見ないため絶対パスで扱う
$versionPath = Join-Path $repoRoot "mosaic_tool/version.py"
$versionText = [System.IO.File]::ReadAllText($versionPath)
# 末尾を $ で固定すると CRLF のファイルで一致しないため、閉じ引用符までで止める
$nameMatch = [regex]::Match($versionText, '(?m)^APP_NAME\s*=\s*"([^"]*)"')
$versionMatch = [regex]::Match($versionText, '(?m)^__version__\s*=\s*"([^"]*)"')
if (-not $nameMatch.Success -or -not $versionMatch.Success) {
    throw "mosaic_tool/version.py から APP_NAME / __version__ を読み取れませんでした: $versionPath"
}
$appName = $nameMatch.Groups[1].Value
$appVersion = $versionMatch.Groups[1].Value
# 配布物の命名規則はこのスクリプトを唯一の情報源とする (release.yml では組み立てない)
$packageName = "$appName-v$appVersion-win-x64"

# 同梱する README はビルド前に確認する (ビルド後に失敗すると数分を無駄にする)
$readmePath = Join-Path $repoRoot "README.md"
if (-not (Test-Path -LiteralPath $readmePath)) {
    throw "同梱する README.md が見つかりません: $readmePath"
}

Write-Host "== $packageName をパッケージします ==" -ForegroundColor Cyan

# exe のビルドは build.ps1 に任せる (配布は onefile 形式のみ)
# 名前付きパラメータとして渡すため、配列ではなくハッシュテーブルでスプラッティングする
$buildArgs = @{}
if ($Python) { $buildArgs["Python"] = $Python }
if ($Clean) { $buildArgs["Clean"] = $true }
& (Join-Path $PSScriptRoot "build.ps1") @buildArgs

$exePath = Join-Path $repoRoot "dist\$appName.exe"
if (-not (Test-Path -LiteralPath $exePath)) {
    throw "ビルドした実行ファイルが見つかりません: $exePath"
}

# 展開時に中身が散らばらないよう、zip 内へトップレベルフォルダを 1 つ作る
# ステージング先ごと作り直して、古いバージョンの残骸を zip に混ぜない
$stageRoot = Join-Path $repoRoot "build\package"
if (Test-Path -LiteralPath $stageRoot) {
    Remove-Item -Recurse -Force -LiteralPath $stageRoot
}
$stageDir = Join-Path $stageRoot $packageName
New-Item -ItemType Directory -Path $stageDir -Force | Out-Null
Copy-Item -LiteralPath $exePath -Destination $stageDir
Copy-Item -LiteralPath $readmePath -Destination $stageDir

# Compress-Archive も .NET Framework の CreateFromDirectory も、Windows ではパス区切りに \ を
# 書き込んで ZIP 仕様に反する。他 OS や 7-Zip でも展開できるよう、エントリ名を明示して作る
$zipPath = Join-Path $repoRoot "dist\$packageName.zip"
if (Test-Path -LiteralPath $zipPath) {
    Remove-Item -Force -LiteralPath $zipPath
}
Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem
$zip = [System.IO.Compression.ZipFile]::Open($zipPath, [System.IO.Compression.ZipArchiveMode]::Create)
try {
    foreach ($file in Get-ChildItem -LiteralPath $stageDir -Recurse -File) {
        $relative = $file.FullName.Substring($stageRoot.Length).TrimStart('\') -replace '\\', '/'
        [System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile(
            $zip, $file.FullName, $relative,
            [System.IO.Compression.CompressionLevel]::Optimal) | Out-Null
    }
} finally {
    $zip.Dispose()
}

Write-Host "== 完了: $zipPath ==" -ForegroundColor Green
```

ファイルは既存の PowerShell スクリプトに合わせて **UTF-8 BOM 付き**で保存する。BOM が無いと Windows PowerShell 5.1 が ANSI として読み、日本語のコメントとエラーメッセージが化ける。BOM なしで書いてしまった場合は以下で変換する。

```powershell
$p = 'scripts\package.ps1'
$t = [System.IO.File]::ReadAllText($p)
[System.IO.File]::WriteAllText($p, $t, [System.Text.UTF8Encoding]::new($true))
```

- [ ] **Step 2: `justfile` に `package` レシピを追加する**

`build` レシピ（`{{_ps}} scripts/build.ps1 {{ARGS}}` の行）の直後、`bump` レシピのコメント行の前に以下を挿入する。

```
# 配布用 zip をローカルで作成する (例: just package -Clean)
package *ARGS:
    {{_ps}} scripts/package.ps1 {{ARGS}}
```

- [ ] **Step 3: README.md 不在時にビルド前で中断することを確認する**

```powershell
Move-Item -LiteralPath README.md -Destination README.md.bak
powershell -ExecutionPolicy Bypass -File scripts/package.ps1
Move-Item -LiteralPath README.md.bak -Destination README.md
```

期待: `同梱する README.md が見つかりません: ...README.md` で即座に失敗する。PyInstaller のインストールやビルドは走らない。最後の `Move-Item` で README.md が元に戻っていること。

- [ ] **Step 4: zip の生成を確認する**

Run: `just package -Clean`

期待: 最終行が `== 完了: ...\dist\MosaicTool-v1.0.0-win-x64.zip ==`。

- [ ] **Step 5: zip の中身を確認する**

```powershell
Add-Type -AssemblyName System.IO.Compression.FileSystem
[System.IO.Compression.ZipFile]::OpenRead((Resolve-Path dist\MosaicTool-v1.0.0-win-x64.zip)).Entries | ForEach-Object { $_.FullName }
```

期待: 次の 2 エントリのみ。

```
MosaicTool-v1.0.0-win-x64/MosaicTool.exe
MosaicTool-v1.0.0-win-x64/README.md
```

- [ ] **Step 6: 展開した exe が起動することを確認する**

```powershell
Expand-Archive -Path dist\MosaicTool-v1.0.0-win-x64.zip -DestinationPath $env:TEMP\mosaic-pkg-check -Force
& "$env:TEMP\mosaic-pkg-check\MosaicTool-v1.0.0-win-x64\MosaicTool.exe" assets\sample.png
```

期待: ウィンドウが開き、`assets\sample.png` が表示される。確認したらウィンドウを閉じる。

- [ ] **Step 7: コミット**

```bash
git add scripts/package.ps1 justfile
git commit -m "feat(release): 配布用 zip を作る package.ps1 を追加"
```

---

### Task 2: README を利用者向けに再編し docs/development.md を追加

**Files:**
- Create: `docs/development.md`
- Modify: `README.md`（全面書き換え）

**Interfaces:**
- Consumes: Task 1 の `scripts/package.ps1` と `just package`（`docs/development.md` に記述する）
- Produces: zip へ同梱される `README.md`。Task 1 の `package.ps1` はこのファイルをコピーする。

- [ ] **Step 1: `docs/development.md` を作成する**

内容は現行 `README.md` からの移設が中心。以下をそのまま書き込む。

````markdown
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
````

- [ ] **Step 2: `README.md` を利用者向けに全面書き換えする**

以下の内容でファイル全体を置き換える。「操作」「保存先」「設定の保存」は現行 README の内容をそのまま引き継いでいる。

````markdown
# MosaicTool

画像の指定範囲にモザイクをかけるツール。

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

## 保存先

- ファイル単体: 同じ場所に `名前_mc.拡張子`
- フォルダ: 隣に `フォルダ名_mc\` を作成し 1 枚ごとに保存(保存後、自動で次の画像へ)

モザイクサイズは 5〜100px(5px 刻み)、1 枚の画像内で共通です。

## 設定の保存

以下はアプリ設定として保存され、次回起動時に復元されます(Windows ではレジストリ `HKEY_CURRENT_USER\Software\MosaicTool\MosaicTool`)。

変更の都度、即時保存されるもの:

- モザイクサイズ
- ペン太さ
- 自動保存の ON/OFF
- ツールモード(ペン / 矩形、既定はペン)

終了時に保存されるもの:

- ウィンドウの位置とサイズ

## 開発者向け

ソースからの起動・ビルド・パッケージング・リリース手順は [docs/development.md](https://github.com/kidonaru/MosaicTool/blob/main/docs/development.md) を参照してください。
````

- [ ] **Step 3: 現行 README の内容が失われていないことを確認する**

Run: `git diff README.md`

期待: 削除されているのは「起動」節の `pip install` 手順、「実行ファイルのビルド」節、「リリース」節、「リポジトリ構成」節のみ。これらはすべて `docs/development.md` に存在すること（`git show HEAD:README.md` と `docs/development.md` を見比べて、移設漏れがないか確認する）。

- [ ] **Step 4: zip 同梱時にリンクが壊れないことを確認する**

Run: `Select-String -Path README.md -Pattern '\]\((?!https://)'`（PowerShell。負の先読みを使うため `grep -E` は不可、bash なら `grep -nP`）

期待: 一致なし。README 内のリンクがすべて絶対 URL であること。

- [ ] **Step 5: コミット**

```bash
git add README.md docs/development.md
git commit -m "docs: README を利用者向けに再編し開発者向け手順を docs/development.md へ移動"
```

---

### Task 3: release.yml を zip 配布に切り替え

**Files:**
- Modify: `.github/workflows/release.yml`

**Interfaces:**
- Consumes: Task 1 の `scripts/package.ps1`（`-Python` / `-Clean` を受け取り `dist/<APP_NAME>-v<バージョン>-win-x64.zip` を生成する）
- Produces: Release アセットとしての zip 1 個。

- [ ] **Step 1: 先頭のコメントと `name` 直上の説明を zip に合わせる**

ファイル冒頭の 4 行を以下に置き換える。

```yaml
# 配布用 zip をビルドして GitHub Releases へアップロードするワークフロー
#
# - タグ push (v*) : zip をビルドし、そのタグの Release を作成してアップロードする
# - 手動実行       : zip をビルドし、Actions の成果物 (Artifact) としてのみ残す
```

- [ ] **Step 2: `meta` ステップから `asset_name` の出力を削除する**

「バージョン情報を取得」ステップの `run` 末尾にある次の 1 行を削除する。

```
          "asset_name=$appName-v$appVersion-win-x64.exe" >> $env:GITHUB_OUTPUT
```

`app_name` / `app_version` の出力と、タグと `mosaic_tool/version.py` の整合チェックはそのまま残す。

- [ ] **Step 3: ビルドステップを package.ps1 に置き換え、リネームステップを削除する**

「exe をビルド」ステップと「成果物をリネーム」ステップ（コメント 2 行を含む）をまとめて、以下の 1 ステップに置き換える。

```yaml
      - name: 配布用 zip をパッケージ
        shell: pwsh
        run: powershell -ExecutionPolicy Bypass -File scripts/package.ps1 -Python python -Clean
```

- [ ] **Step 4: アップロード先を zip に変更する**

`upload-artifact` ステップを以下に置き換える。

```yaml
      - uses: actions/upload-artifact@v4
        with:
          name: ${{ steps.meta.outputs.app_name }}-v${{ steps.meta.outputs.app_version }}-win-x64
          path: dist/*.zip
          if-no-files-found: error
```

`action-gh-release` ステップの `files` を以下に変更する（`name` / `generate_release_notes` / SHA 固定は変更しない）。

```yaml
          files: dist/*.zip
```

- [ ] **Step 5: ワークフローの構文と参照の整合を確認する**

Run: `grep -n "asset_name\|\.exe\|dist/" .github/workflows/release.yml`

期待: `asset_name` の参照が 0 件。`dist/` の参照は `path: dist/*.zip` と `files: dist/*.zip` の 2 件のみ。`.exe` への参照が残っていないこと。

- [ ] **Step 6: コミット**

```bash
git add .github/workflows/release.yml
git commit -m "ci(release): 配布物を exe 単体から zip へ変更"
```

- [ ] **Step 7: 手動実行で CI が通ることを確認する**

変更を push したあと、GitHub の Actions から `release` ワークフローを手動実行（`workflow_dispatch`）する。

期待: ジョブが成功し、Artifacts に `MosaicTool-v<バージョン>-win-x64` が 1 件できる。ダウンロードして中身が `MosaicTool-v<バージョン>-win-x64/` 配下の exe と README.md であること。Release は作成されないこと。

---

## 完了条件

- `just package -Clean` で `dist/MosaicTool-v<バージョン>-win-x64.zip` が生成され、中身が exe と README.md の 2 ファイルのみ。
- README.md が利用者向けの内容のみで、リンクはすべて絶対 URL。
- `docs/development.md` に旧 README の開発者向け内容がすべて移設されている。
- `release.yml` の手動実行が成功し、Artifact が zip になっている。
