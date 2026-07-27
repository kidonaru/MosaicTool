# mosaic_tool/version.py のバージョンでタグを作成して push する (release ワークフローが起動する)
# 使い方: powershell -ExecutionPolicy Bypass -File scripts/tag.ps1
#   -Remote <name> : push 先のリモート (既定: origin)
#   -Branch <name> : リリース元として許可するブランチ (既定: main)
#   -DryRun        : 検証だけ行い、push / タグ作成は行わない
[CmdletBinding()]
param(
    [string]$Remote = "origin",
    [string]$Branch = "main",
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
# スクリプトは scripts/ に置くが、git 操作はリポジトリ直下を基準に行う
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $repoRoot

# mosaic_tool/version.py を直接読む (import すると __pycache__ の古い .pyc を拾うことがある)
# .NET の API は Set-Location を見ないため絶対パスで扱う
$path = Join-Path $repoRoot "mosaic_tool/version.py"
# 末尾を $ で固定すると CRLF のファイルで一致しないため、閉じ引用符までで止める
$match = [regex]::Match([System.IO.File]::ReadAllText($path), '(?m)^__version__\s*=\s*"([^"]*)"')
if (-not $match.Success) {
    throw "mosaic_tool/version.py から __version__ を読み取れませんでした: $path"
}
$tag = "v$($match.Groups[1].Value)"

# 意図しないブランチからリリースしないことを確認する
$currentBranch = (git rev-parse --abbrev-ref HEAD).Trim()
if ($LASTEXITCODE -ne 0) { throw "現在のブランチを取得できませんでした" }
if ($currentBranch -ne $Branch) {
    throw "現在のブランチは $currentBranch です。$Branch から実行するか -Branch で許可してください"
}

if (git status --porcelain --untracked-files=no) {
    throw "未コミットの変更があります。タグはコミット済みの状態で作成してください"
}

# fetch してからローカルを見ることで、リモートにあるタグの重複も検出する
git fetch --tags --quiet $Remote
if ($LASTEXITCODE -ne 0) { throw "git fetch に失敗しました" }
if (git tag --list $tag) {
    throw "タグ $tag は既に存在します。mosaic_tool/version.py のバージョンを上げてください"
}

# 検証はここまでで完了するため、以降の書き込み操作だけを飛ばす
if ($DryRun) {
    Write-Host "== dry-run: 検証のみ実行しました ==" -ForegroundColor Yellow
    Write-Host "作成されるタグ: $tag"
    Write-Host "実行される操作: git push $Remote HEAD / git tag -a $tag / git push $Remote $tag"
    return
}

# タグだけを push してもコミットが無いとビルドできないため、先に HEAD を push する
git push $Remote HEAD
if ($LASTEXITCODE -ne 0) { throw "git push に失敗しました" }

git tag -a $tag -m $tag
if ($LASTEXITCODE -ne 0) { throw "git tag に失敗しました" }

# push に失敗したままローカルのタグが残ると、次回の重複チェックで止まって再実行できなくなる
git push $Remote $tag
if ($LASTEXITCODE -ne 0) {
    git tag -d $tag
    throw "タグの push に失敗しました。作成したローカルのタグ $tag は削除しました"
}

Write-Host "== $tag を push しました ==" -ForegroundColor Green

# リモート URL から Actions のページを案内する
$url = (git remote get-url $Remote).Trim() -replace '\.git$', '' -replace '^git@github\.com:', 'https://github.com/'
if ($url -match '^https://github\.com/') {
    Write-Host "release ワークフローの進行: $url/actions"
}
