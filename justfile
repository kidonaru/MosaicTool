# MosaicTool のリリース作業用タスク (just https://github.com/casey/just)
# 使い方: just --list

# 全レシピを 1 行の python 呼び出しに統一しているため、レシピ実行シェルの差は影響しない
# (just のシェバングレシピは Windows で動かないため使わない)

_default:
    @just --list

# 現在のバージョンを表示する
version:
    @python -c "import mosaic_tool; print(mosaic_tool.__version__)"

# アプリをソースから起動する (例: just run image.png)
run *ARGS:
    python -m mosaic_tool {{ARGS}}

# 実行ファイルをローカルでビルドする (例: just build --clean)
build *ARGS:
    python scripts/build.py {{ARGS}}

# 配布用 zip をローカルで作成する (例: just package --clean)
package *ARGS:
    python scripts/package.py {{ARGS}}

# mosaic_tool/version.py のバージョンを更新してコミットする (例: just bump patch / just bump 1.1.0 / just bump patch --dry-run)
bump VERSION="patch" *ARGS:
    python scripts/bump.py {{VERSION}} {{ARGS}}

# mosaic_tool/version.py のバージョンでタグを作成して push する (例: just tag / just tag --dry-run)
tag *ARGS:
    python scripts/tag.py {{ARGS}}

# bump と tag をまとめて実行する (例: just release patch / just release 1.1.0)
release VERSION="patch": (bump VERSION) tag
