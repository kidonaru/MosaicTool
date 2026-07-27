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
# GitHub Actions から呼ばれた場合は、Artifact 名にも同じ値を使えるよう受け渡す
# >> は Windows PowerShell 5.1 だと UTF-16LE で書き Actions が解釈できないため、UTF-8 を明示する
if ($env:GITHUB_OUTPUT) {
    [System.IO.File]::AppendAllText(
        $env:GITHUB_OUTPUT, "package_name=$packageName`n", [System.Text.UTF8Encoding]::new($false))
}

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
# 旧バージョンの zip を消す (release.yml が dist/*.zip で拾うため、残っていると複数件になる)
Get-ChildItem -LiteralPath (Join-Path $repoRoot "dist") -Filter "*.zip" | Remove-Item -Force
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
