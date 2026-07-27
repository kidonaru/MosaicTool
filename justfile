# MosaicTool のリリース作業用タスク (just https://github.com/casey/just)
# 使い方: just --list

# Windows では sh が無いので PowerShell をレシピ実行シェルに使う
set windows-shell := ["powershell.exe", "-NoLogo", "-NoProfile", "-Command"]

# just のシェバングレシピ (#!) は Windows でシェバング行を「インタープリタ + 引数 1 個」として
# 扱うため使えない。処理は .ps1 に置き、レシピからは -File で呼び出す
_ps := "powershell -ExecutionPolicy Bypass -File"

_default:
    @just --list

# 現在のバージョンを表示する
version:
    @python -c "import mosaic_tool; print(mosaic_tool.__version__)"

# アプリをソースから起動する (例: just run image.png)
run *ARGS:
    python -m mosaic_tool {{ARGS}}

# exe をローカルでビルドする (例: just build -Clean)
build *ARGS:
    {{_ps}} scripts/build.ps1 {{ARGS}}

# 配布用 zip をローカルで作成する (例: just package -Clean)
package *ARGS:
    {{_ps}} scripts/package.ps1 {{ARGS}}

# mosaic_tool/version.py のバージョンを更新してコミットする (例: just bump patch / just bump 1.1.0 / just bump patch -DryRun)
bump VERSION="patch" *ARGS:
    {{_ps}} scripts/bump.ps1 {{VERSION}} {{ARGS}}

# mosaic_tool/version.py のバージョンでタグを作成して push する (例: just tag / just tag -DryRun)
tag *ARGS:
    {{_ps}} scripts/tag.ps1 {{ARGS}}

# bump と tag をまとめて実行する (例: just release patch / just release 1.1.0)
release VERSION="patch": (bump VERSION) tag
