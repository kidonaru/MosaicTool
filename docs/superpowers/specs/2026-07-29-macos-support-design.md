# macOS (Apple Silicon) 版対応 設計

- 日付: 2026-07-29
- 対象バージョン: v1.1.0 の次のリリース
- ステータス: 承認済み（実装計画待ち）

## 背景と目的

MosaicTool は現在 Windows 専用として配布されている。ビルド・パッケージング・
リリースの一式が PowerShell スクリプトと Windows 前提のパス解決に依存しており、
macOS では起動もビルドもできない。

本設計は、Windows 版と同等の体験を macOS (Apple Silicon) で提供することを目的とする。
配布物の作成、GitHub Releases への掲載、自動検出機能の動作までを対象に含める。

## スコープ

### 対象に含む

- macOS 向け `.app` バンドルのビルドと zip 配布
- GitHub Actions での macOS ビルドジョブと Release 掲載
- 自動検出（uv / venv / ultralytics / torch）の macOS 対応と MPS 推論
- Developer ID 署名・公証（証明書未設定時は ad-hoc へフォールバック）
- ビルド・リリーススクリプトの Python への統一
- Finder からのファイルオープン連携

### 対象に含まない

- Intel Mac (x86_64) および universal2 バイナリ
- Linux 対応
- Homebrew Cask や Mac App Store での配布
- アプリの編集機能・UI の変更（描画・モザイク処理ロジックは無変更）

## 決定事項

| 項目 | 決定 | 理由 |
|---|---|---|
| 対象アーキテクチャ | Apple Silicon (arm64) のみ | `macos-latest` が arm64 のため CI が 1 ジョブで済む。PySide6 が universal2 wheel を提供しないため universal2 は実現不可 |
| データ配置 | `~/Library/Application Support/MosaicTool/` | `.app` を `/Applications` へ移しても動作し、アプリ更新時に数 GB の再ダウンロードが不要 |
| 署名 | Developer ID 署名 + 公証（Secrets 未設定時は ad-hoc） | 証明書はこれから取得するため、取得前後のどちらでもビルドが通る必要がある |
| 推論デバイス | MPS を使用し、CPU/GPU 選択は macOS では非表示 | macOS の torch は通常の PyPI wheel が MPS 対応済みで、インストール内容が 1 通りしかない |
| スクリプト構成 | Python に統一し `.ps1` を置き換える | 同一ロジックを 2 言語で保守することを避ける |
| 配布形式 | zip | Windows 版と命名・手順が揃う |
| バンドル形式 | onedir (`.app`) | onefile は毎回一時展開するため公証との相性が悪く、起動も遅い |

## アーキテクチャ

OS 差分は既存の境界モジュールに閉じ込める。描画・編集ロジック
（`canvas.py` / `mosaic.py` / `regions.py`）は変更しない。新規モジュールは作らず、
分岐は各モジュール内の名前付き関数として明示する。

| ファイル | 差分の内容 |
|---|---|
| `mosaic_tool/detect/paths.py` | データ配置基準、venv の Python パス、uv の実行ファイル名 |
| `mosaic_tool/detect/runtime.py` | CUDA インデックスの有無、推論デバイスの解決 |
| `mosaic_tool/detect/setup_dialog.py` | macOS では CPU/GPU 選択を非表示 |
| `mosaic_tool/resources.py` | `.app` バンドル内でのアイコン解決 |
| `mosaic_tool/app.py` | Finder からの「開く」イベント処理 |

## コンポーネント設計

### 1. パス解決 (`detect/paths.py`)

`base_dir()` に OS 分岐を入れる。Windows の挙動は変えない（既存ユーザーの
`models/` `runtime/` が見えなくなるのを避けるため）。

| 実行形態 | `base_dir()` |
|---|---|
| frozen + macOS | `~/Library/Application Support/MosaicTool/` |
| frozen + Windows | 実行ファイルの隣（現状維持） |
| ソース実行（両 OS） | リポジトリ直下（現状維持） |

macOS では初回アクセス時にディレクトリが存在しないため、`models_dir()` /
`runtime_dir()` を使う側で `mkdir(parents=True, exist_ok=True)` を行う。

`venv_python()`:

- Windows: `runtime/Scripts/python.exe`
- macOS: `runtime/bin/python`

同梱する uv の実行ファイル名は Windows が `uv.exe`、macOS が `uv`。
PyInstaller の `--add-data` は実行ビットを保持しないため、macOS では
uv を使う直前に `os.chmod(uv, 0o755)` を行う。

`_bundle_dir()` は変更不要。macOS の onedir `.app` では `sys._MEIPASS` が
`MosaicTool.app/Contents/Frameworks` を指し、同梱物はその配下に置かれる。

### 2. 推論環境 (`detect/runtime.py`)

`install_command()` は macOS では `--extra-index-url`（cu121）を付けない。
macOS の torch は PyPI の通常 wheel が MPS 対応済みで、CUDA ビルドは存在しない。

推論デバイスの解決を新関数 `resolve_device(setting: str) -> str` として切り出す
（現在は `app.py` にインラインで書かれている）。

| 設定値 | macOS | Windows |
|---|---|---|
| `auto` | `"mps"` | `""`（ultralytics の自動選択） |
| `cpu` | `"cpu"` | `"cpu"` |

`has_nvidia_gpu()` は Windows 専用のヒントとして残すが、macOS では呼ばれない。

### 3. セットアップダイアログ (`detect/setup_dialog.py`)

macOS では CPU/GPU のラジオボタンを生成せず、「推論環境をインストール」の
単一動作にする。`RuntimeInstaller.start()` へは `use_gpu=False` を渡す。

### 4. アイコンと Finder 連携

- `assets/icon.icns` を追加する。マスターは既存の `assets/icon.png`。
  `scripts/icon_assets.py` に `.icns` の生成と検証を追加し、テストで担保する。
- `resources.py` の `app_icon_path()` は現在 `__file__` を基準にしているが、
  PyInstaller ではパッケージが PYZ に取り込まれ `__file__` が実在しない。
  `detect/paths.py` の `_bundle_dir()` と同じく `sys._MEIPASS` を基準に解決する。
- `Info.plist` に `CFBundleDocumentTypes`（対応画像形式）を追加し、
  `QApplication` で `QFileOpenEvent` を処理して画像を開けるようにする。
  これは Windows の「exe へのドラッグ&ドロップ」に相当する体験。
  ウィンドウへのドラッグ&ドロップは Qt が吸収するため変更不要。

### 5. ビルド・パッケージスクリプトの Python 化

`scripts/*.ps1`（5 本）を Python へ移植し、`justfile` は `python scripts/*.py` を
呼ぶだけにする。既存の PowerShell スクリプトが持つオプションは維持する。

| 新スクリプト | 置き換え対象 | 維持するオプション |
|---|---|---|
| `scripts/build.py` | `build.ps1` | `--python` / `--onedir` / `--clean` / `--uv-version` |
| `scripts/package.py` | `package.ps1` | `--python` / `--clean` |
| `scripts/bump.py` | `bump.ps1` | 位置引数（`patch`/`minor`/`major`/`x.y.z`）/ `--dry-run` |
| `scripts/tag.py` | `tag.ps1` | `--remote` / `--branch` / `--dry-run` |

OS 差分は関数内で分岐する。

| 差分 | Windows | macOS |
|---|---|---|
| uv アセット | `uv-x86_64-pc-windows-msvc.zip` | `uv-aarch64-apple-darwin.tar.gz` |
| PyInstaller 出力形式 | `--onefile` | `--onedir`（`--windowed` により `.app` 生成） |
| アイコン | `assets/icon.ico` | `assets/icon.icns` |
| `--add-data` 区切り | `os.pathsep` を使い、スクリプト側では意識しない | 同左 |
| 成果物名 | `MosaicTool-v<ver>-win-x64.zip` | `MosaicTool-v<ver>-mac-arm64.zip` |

zip 化は macOS では `ditto -c -k --sequesterRsrc --keepParent` を使う。
Python の `zipfile` は `.app` 内の実行ビットとシンボリックリンクを保持できず、
展開したアプリが起動しなくなる。Windows 側は既存の実装（エントリ名を `/` で
明示する方式）を Python へそのまま移植する。

配布物の命名規則は `scripts/package.py` を唯一の情報源とし、
`GITHUB_OUTPUT` へ `package_name` を書き出す点も現行を踏襲する。

### 6. 署名・公証

PyInstaller の `--codesign-identity` と `--osx-entitlements-file` で署名し、
その後 `notarytool submit --wait` → `stapler staple` → 再 zip の順に処理する。

Hardened Runtime 下で venv の Python を子プロセスとして起動するため、
entitlements に以下を付与する。

- `com.apple.security.cs.allow-jit`
- `com.apple.security.cs.allow-unsigned-executable-memory`
- `com.apple.security.cs.disable-library-validation`

必要な GitHub Secrets:

| 名前 | 内容 |
|---|---|
| `MACOS_CERTIFICATE` | Developer ID Application 証明書（.p12 を base64 化） |
| `MACOS_CERTIFICATE_PWD` | .p12 のパスワード |
| `MACOS_SIGN_IDENTITY` | 署名 ID（例: `Developer ID Application: Name (TEAMID)`） |
| `MACOS_TEAM_ID` | Team ID |
| `MACOS_NOTARY_APPLE_ID` | 公証に使う Apple ID |
| `MACOS_NOTARY_PASSWORD` | App-specific password |

**Secrets が未設定の場合は ad-hoc 署名でビルドし、公証をスキップする。**
証明書の取得手順と Secrets の登録方法は `docs/macos-signing.md` に残す。
ad-hoc の場合に必要な回避手順（`xattr -dr com.apple.quarantine MosaicTool.app`）も
併記し、署名が有効になった時点で README から削除できるようにする。

### 7. CI (`.github/workflows/release.yml`)

現行は `build-windows` ジョブ内で Release を作成しているため、ジョブが 2 つに
なると同じ Release への同時書き込みが競合する。Release 作成を独立したジョブに
分離する。

```
build-windows ─┐
               ├─→ release  (needs: 両方 / タグ push 時のみ)
build-macos  ──┘   ※ runs-on: macos-latest (arm64)
```

- `build-*` は zip を Artifact としてアップロードする（現行どおり）
- `release` は両 Artifact をダウンロードし、`softprops/action-gh-release` へまとめて渡す
- タグと `mosaic_tool/version.py` の整合検証は独立した `meta` ジョブに置き、
  `build-*` は `needs: meta` とする。不一致なら 2 つのビルドを始める前に落ちる
- サードパーティ Action は現行と同じく commit SHA で固定する

## エラー処理

| 状況 | 挙動 |
|---|---|
| `~/Library/Application Support/` へ書き込めない | セットアップダイアログでパスを含むエラーを表示し、中断する |
| uv に実行権限が無い | 使用直前に chmod する。失敗したらパスを含むエラーを表示 |
| MPS が使えない環境（古い macOS 等） | ワーカーが例外を投げるため、既存の `failed` シグナル経由でメッセージを表示。設定で `cpu` を選べば回避できる |
| Secrets 未設定でのビルド | エラーにせず ad-hoc 署名にフォールバックし、公証をスキップしたことをログに出す |
| 公証の失敗 | CI をエラーで落とす（署名済みのつもりの壊れた成果物を配らない） |

## テスト

### 修正が必要な既存テスト

- `tests/test_detect_worker_client.py` に `C:/rt/python.exe` のハードコードがあり、
  OS 非依存の形へ直す。

### 新規テスト

- `detect/paths.py`: frozen / ソース実行 × macOS / Windows の `base_dir()` 分岐、
  `venv_python()` の分岐、uv の実行ファイル名（`sys.platform` と `sys.frozen` を
  monkeypatch する）
- `detect/runtime.py`: `install_command()` の `--extra-index-url` 有無、
  `resolve_device()` の全組み合わせ
- `scripts/package.py`: 配布物名の組み立て（`-win-x64` / `-mac-arm64`）
- `scripts/icon_assets.py`: `.icns` に必要なサイズが含まれること

### 手動検証

- macOS 上でのローカルビルド、`.app` の起動、画像の編集と保存
- Finder から画像を `.app` にドロップして開けること
- ad-hoc 署名した zip を展開して起動できること

## ドキュメント

| ファイル | 更新内容 |
|---|---|
| `README.md` | macOS の導入手順、`Cmd` キー表記の併記、保存先、自動検出のダウンロードサイズ（macOS は単一構成） |
| `docs/development.md` | Python スクリプトへの移行、macOS ビルド手順、リポジトリ構成の記述更新 |
| `docs/macos-signing.md` | 新規。証明書の取得、.p12 の書き出し、Secrets の登録手順 |

## リスクと未検証事項

| 項目 | 状況 |
|---|---|
| Developer ID 署名・公証 | 証明書が未取得のため、コードパスは実装できるが実行確認ができない。Secrets 投入後の初回リリースで確認する |
| MPS での実推論 | 検証には torch とモデル（数百 MB）のダウンロードが必要。実施可否は実装中に判断する |
| Hardened Runtime 下での子プロセス起動 | entitlements の指定が十分か、公証済みビルドでなければ最終確認できない |
| Windows 側のリグレッション | スクリプトを Python へ移植するため、Windows での再検証が必要。CI の手動実行で確認する |
