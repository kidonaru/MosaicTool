# MosaicTool の実行ファイル (exe) を PyInstaller でビルドする
# 使い方: powershell -ExecutionPolicy Bypass -File scripts/build.ps1
#   -Python <path>  : 使用する Python を指定 (既定: py -3 があればそれ、なければ python)
#   -OneDir         : 1 ファイルではなくフォルダ形式で出力 (起動が速い)
#   -Clean          : build/ dist/ を削除してからビルド
[CmdletBinding()]
param(
    [string]$Python,
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

# ビルド本体
$mode = if ($OneDir) { "--onedir" } else { "--onefile" }
$iconPath = Join-Path $repoRoot "assets\icon.ico"
if (-not (Test-Path -LiteralPath $iconPath)) {
    throw "アプリアイコンが見つかりません: $iconPath"
}
$options = @(
    "-m", "PyInstaller",
    "--noconfirm",
    "--clean",
    $mode,
    "--windowed",              # コンソールウィンドウを出さない
    "--name", $appName,
    "--specpath", "build",     # .spec をリポジトリ直下に置かない
    "--icon", $iconPath,
    "--add-data", "${iconPath}:assets",   # mosaic_tool/resources.py が assets/icon.ico を参照する
    "--paths", ".",            # mosaic_tool パッケージをリポジトリ直下から解決する
    "mosaic_tool/__main__.py"
)
Invoke-Python $options

$output = if ($OneDir) { "dist\$appName\$appName.exe" } else { "dist\$appName.exe" }
if (-not (Test-Path $output)) {
    throw "ビルドは完了しましたが実行ファイルが見つかりません: $output"
}
Write-Host "== 完了: $output ==" -ForegroundColor Green
