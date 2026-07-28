# MosaicTool の実行ファイル (exe) を PyInstaller でビルドする
# 使い方: powershell -ExecutionPolicy Bypass -File scripts/build.ps1
#   -Python <path>  : 使用する Python を指定 (既定: py -3 があればそれ、なければ python)
#   -OneDir         : 1 ファイルではなくフォルダ形式で出力 (起動が速い)
#   -Clean          : build/ dist/ を削除してからビルド
[CmdletBinding()]
param(
    [string]$Python,
    [string]$UvVersion = "latest",
    [switch]$OneDir,
    [switch]$Clean
)

$ErrorActionPreference = "Stop"
# スクリプトは scripts/ に置くが、ビルドはリポジトリ直下を基準に行う
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $repoRoot

# 使用する Python を決定する
if (-not $Python) {
    if (Test-Path ".venv\Scripts\python.exe") {
        $Python = ".venv\Scripts\python.exe"
    } elseif (Get-Command py -ErrorAction SilentlyContinue) {
        $Python = "py"
    } else {
        $Python = "python"
    }
}
# py ランチャー経由なら Python 3 を明示する (配列型を保つため代入は分けて書く)
[string[]]$pyArgs = @()
if ($Python -eq "py") { $pyArgs = @("-3") }

function Invoke-Python {
    param([string[]]$Arguments)
    & $Python @pyArgs @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "コマンドが失敗しました: $Python $(($pyArgs + $Arguments) -join ' ')"
    }
}

# mosaic_tool/version.py からアプリ名とバージョンを取得する (ハードコードを避ける)
$appName = (& $Python @pyArgs -c "import mosaic_tool; print(mosaic_tool.APP_NAME)").Trim()
$appVersion = (& $Python @pyArgs -c "import mosaic_tool; print(mosaic_tool.__version__)").Trim()
if ($LASTEXITCODE -ne 0 -or -not $appName) {
    throw "mosaic_tool/version.py からバージョン情報を取得できませんでした"
}
Write-Host "== $appName v$appVersion をビルドします ($Python) ==" -ForegroundColor Cyan

if ($Clean) {
    Write-Host "-- build/ dist/ を削除します"
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue build, dist
}

# 依存関係と PyInstaller を用意する
Write-Host "-- 依存関係をインストールします"
Invoke-Python @("-m", "pip", "install", "--upgrade", "pip")
Invoke-Python @("-m", "pip", "install", "-r", "requirements.txt")
Invoke-Python @("-m", "pip", "install", "pyinstaller")

# 自動検出のセットアップに使う uv を取得して同梱する
# (ユーザーの環境に Python が無くても venv を用意できるようにするため)
$uvDir = Join-Path $repoRoot "build\uv"
$uvExe = Join-Path $uvDir "uv.exe"
if (-not (Test-Path -LiteralPath $uvExe)) {
    Write-Host "-- uv ($UvVersion) を取得します"
    New-Item -ItemType Directory -Path $uvDir -Force | Out-Null
    $uvAsset = "uv-x86_64-pc-windows-msvc.zip"
    $uvUrl = if ($UvVersion -eq "latest") {
        "https://github.com/astral-sh/uv/releases/latest/download/$uvAsset"
    } else {
        "https://github.com/astral-sh/uv/releases/download/$UvVersion/$uvAsset"
    }
    $uvZip = Join-Path $uvDir $uvAsset
    Invoke-WebRequest -Uri $uvUrl -OutFile $uvZip
    Expand-Archive -LiteralPath $uvZip -DestinationPath $uvDir -Force
    Remove-Item -LiteralPath $uvZip -Force
}
if (-not (Test-Path -LiteralPath $uvExe)) {
    throw "uv.exe を取得できませんでした: $uvDir"
}

$workerScript = Join-Path $repoRoot "mosaic_tool\detect\worker_main.py"
if (-not (Test-Path -LiteralPath $workerScript)) {
    throw "検出ワーカーが見つかりません: $workerScript"
}

# ビルド本体
$mode = if ($OneDir) { "--onedir" } else { "--onefile" }
$iconPath = Join-Path $repoRoot "assets\icon.ico"
if (-not (Test-Path -LiteralPath $iconPath)) {
    throw "アプリアイコンが見つかりません: $iconPath"
}
# .spec は一度生成してから編集する(Qt の OpenSSL バックエンドを外すため)。
# ビルド用の --noconfirm / --clean は makespec が受け付けないので分けて渡す
$specOptions = @(
    "-m", "PyInstaller.utils.cliutils.makespec",
    $mode,
    "--windowed",              # コンソールウィンドウを出さない
    "--name", $appName,
    "--specpath", "build",     # .spec をリポジトリ直下に置かない
    "--icon", $iconPath,
    "--add-data", "${iconPath}:assets",   # mosaic_tool/resources.py が assets/icon.ico を参照する
    "--add-data", "${uvExe}:.",           # mosaic_tool/detect/paths.py が展開先ルートの uv.exe を参照する
    # ワーカーは venv の Python へスクリプトのパスとして渡すため、
    # PYZ に取り込まれるだけでは足りず .py の実体も同梱する
    "--add-data", "${workerScript}:mosaic_tool/detect",
    "--paths", ".",            # mosaic_tool パッケージをリポジトリ直下から解決する
    "mosaic_tool/__main__.py"
)
Invoke-Python $specOptions

$specPath = Join-Path $repoRoot "build\$appName.spec"
if (-not (Test-Path -LiteralPath $specPath)) {
    throw "spec が生成されませんでした: $specPath"
}
Write-Host "-- Qt の OpenSSL バックエンドを除外します"
Invoke-Python @((Join-Path $PSScriptRoot "exclude_openssl_backend.py"), $specPath)

Invoke-Python @("-m", "PyInstaller", "--noconfirm", "--clean", $specPath)

$output = if ($OneDir) { "dist\$appName\$appName.exe" } else { "dist\$appName.exe" }
if (-not (Test-Path $output)) {
    throw "ビルドは完了しましたが実行ファイルが見つかりません: $output"
}

# 同梱された OpenSSL の組み合わせを検証する
# (libssl と libcrypto が別ディレクトリから拾われると実行時にシンボル欠落で落ちる)
$tocPath = Join-Path $repoRoot "build\$appName\EXE-00.toc"
if (-not (Test-Path -LiteralPath $tocPath)) {
    # 検証を黙って飛ばすと壊れた exe をそのまま配布してしまうため、ここで止める
    throw "TOC が見つからず OpenSSL を検証できません (PyInstaller の出力形式が変わった可能性): $tocPath"
}
Write-Host "-- 同梱された OpenSSL を検証します"
Invoke-Python @((Join-Path $PSScriptRoot "check_bundled_openssl.py"), $tocPath)
Write-Host "== 完了: $output ==" -ForegroundColor Green
