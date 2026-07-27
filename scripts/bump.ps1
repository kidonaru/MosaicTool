# mosaic_tool/version.py のバージョンを更新してコミットする
# 使い方: powershell -ExecutionPolicy Bypass -File scripts/bump.ps1 patch
#   patch / minor / major : 現在のバージョンから該当箇所を 1 上げる
#   x.y.z                 : そのバージョンを直接指定する
#   -DryRun               : 検証だけ行い、ファイル書き換えとコミットは行わない
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string]$Version,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
# スクリプトは scripts/ に置くが、git 操作はリポジトリ直下を基準に行う
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $repoRoot

# version.py 以外の未コミット変更があると、リリースと無関係な変更を巻き込むため中断する
$dirty = git status --porcelain --untracked-files=no | Where-Object { $_ -notmatch '\smosaic_tool/version\.py$' }
if ($dirty) {
    throw "未コミットの変更があります。コミットまたは退避してから実行してください:`n$($dirty -join "`n")"
}

# mosaic_tool/version.py を直接読む (import すると __pycache__ の古い .pyc を拾うことがある)
# .NET の API は Set-Location を見ないため絶対パスで扱う
$path = Join-Path $repoRoot "mosaic_tool/version.py"
$text = [System.IO.File]::ReadAllText($path)
# 行末を含めないパターンにする (末尾を $ で固定すると CRLF のファイルで一致しない)
$pattern = '(?m)^__version__\s*=\s*"([^"]*)"'
$match = [regex]::Match($text, $pattern)
if (-not $match.Success) {
    throw "mosaic_tool/version.py から __version__ を読み取れませんでした: $path"
}
$current = $match.Groups[1].Value
if ($current -notmatch '^\d+\.\d+\.\d+$') {
    throw "mosaic_tool/version.py の現在のバージョンが x.y.z 形式ではありません: $current"
}

# patch / minor / major 指定を実際のバージョン番号へ解決する
$parts = [int[]]($current -split '\.')
switch ($Version.ToLowerInvariant()) {
    "major" { $target = "$($parts[0] + 1).0.0" }
    "minor" { $target = "$($parts[0]).$($parts[1] + 1).0" }
    "patch" { $target = "$($parts[0]).$($parts[1]).$($parts[2] + 1)" }
    default {
        if ($Version -notmatch '^\d+\.\d+\.\d+$') {
            throw "バージョンは patch / minor / major または x.y.z 形式で指定してください: $Version"
        }
        $target = $Version
    }
}
if ($current -eq $target) {
    throw "バージョンは既に $target です"
}

# 検証はここまでで完了するため、以降の書き込み操作だけを飛ばす
if ($DryRun) {
    Write-Host "== dry-run: 検証のみ実行しました ==" -ForegroundColor Yellow
    Write-Host "更新されるバージョン: v$current -> v$target"
    Write-Host "実行される操作: mosaic_tool/version.py の書き換え / git commit"
    return
}

# __version__ の行だけを書き換える (BOM なし UTF-8 / 既存の改行コードを維持)
$updated = [regex]::Replace($text, $pattern, "__version__ = `"$target`"")
[System.IO.File]::WriteAllText($path, $updated, (New-Object System.Text.UTF8Encoding $false))

git add -- $path
if ($LASTEXITCODE -ne 0) { throw "git add に失敗しました" }
git commit -m "chore(release): v$target にバージョンを更新"
if ($LASTEXITCODE -ne 0) { throw "git commit に失敗しました" }

Write-Host "== v$current -> v$target をコミットしました ==" -ForegroundColor Green
Write-Host "次: just tag (または powershell -ExecutionPolicy Bypass -File scripts/tag.ps1)"
