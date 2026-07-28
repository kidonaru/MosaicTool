"""横幅が足りないとき自動で折り返すツールバー

QToolBar は折り返しに対応しておらず、はみ出した項目は右端の拡張ボタン (>>) に
隠れてしまう。ウィンドウを狭めても全項目を見せたいので、行送りするレイアウト
(FlowLayout) を持つ独自ウィジェットで置き換える。
"""
from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, QSize, Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QFrame,
    QLayout,
    QSizePolicy,
    QToolButton,
    QWidget,
)

TOOLBAR_MARGIN = 4      # ツールバーの外周余白 (px)
TOOLBAR_SPACING = 4     # 項目同士の間隔 (px)
SEPARATOR_HEIGHT = 20   # 区切り線の高さ (px)
SEPARATOR_WIDTH = 1     # 区切り線の太さ (px)
SEPARATOR_COLOR = "#9a9a9a"  # 区切り線の色


class FlowLayout(QLayout):
    """左から順に並べ、行幅を超えたら次の行へ送るレイアウト"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._items: list = []
        self.setContentsMargins(
            TOOLBAR_MARGIN, TOOLBAR_MARGIN, TOOLBAR_MARGIN, TOOLBAR_MARGIN
        )
        self.setSpacing(TOOLBAR_SPACING)

    # --- QLayout の必須オーバーライド ---

    def addItem(self, item) -> None:  # noqa: N802 (Qt のオーバーライド)
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int):  # noqa: N802 (Qt のオーバーライド)
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index: int):  # noqa: N802 (Qt のオーバーライド)
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    # --- 折り返しの実装 ---

    def expandingDirections(self):  # noqa: N802 (Qt のオーバーライド)
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:  # noqa: N802 (Qt のオーバーライド)
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802 (Qt のオーバーライド)
        return self._do_layout(QRect(0, 0, width, 0), test_only=True)

    def setGeometry(self, rect: QRect) -> None:  # noqa: N802 (Qt のオーバーライド)
        super().setGeometry(rect)
        self._do_layout(rect, test_only=False)

    def sizeHint(self) -> QSize:  # noqa: N802 (Qt のオーバーライド)
        # 1 行に収まる幅を要求すると折り返せなくなるため、最小サイズを返して
        # 実際の高さは heightForWidth に委ねる
        return self.minimumSize()

    def minimumSize(self) -> QSize:  # noqa: N802 (Qt のオーバーライド)
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        return size + QSize(
            margins.left() + margins.right(), margins.top() + margins.bottom()
        )

    def _do_layout(self, rect: QRect, *, test_only: bool) -> int:
        """項目を行ごとに割り付け、必要な高さを返す

        test_only の場合は高さの計算だけを行い、実際の配置は変更しない。
        """
        margins = self.contentsMargins()
        area = rect.adjusted(
            margins.left(), margins.top(), -margins.right(), -margins.bottom()
        )
        spacing = self.spacing()
        rows: list[tuple[list[tuple[object, int, QSize]], int]] = []
        current: list[tuple[object, int, QSize]] = []
        row_height = 0
        x = area.x()
        for item in self._items:
            hint = item.sizeHint()
            # 行頭の 1 項目は幅を超えても送らない(無限ループを避けるため)
            if current and x + hint.width() > area.right():
                rows.append((current, row_height))
                current = []
                row_height = 0
                x = area.x()
            current.append((item, x, hint))
            x += hint.width() + spacing
            row_height = max(row_height, hint.height())
        if current:
            rows.append((current, row_height))

        y = area.y()
        for items, height in rows:
            if not test_only:
                for item, item_x, hint in items:
                    # 高さの異なる項目が混在するので行内で縦中央に揃える
                    item_y = y + (height - hint.height()) // 2
                    item.setGeometry(QRect(QPoint(item_x, item_y), hint))
            y += height + spacing
        if not rows:
            return margins.top() + margins.bottom()
        return y - spacing - rect.y() + margins.bottom()


class FlowToolBar(QWidget):
    """QToolBar の代わりに使う、折り返し対応のツールバー

    addAction() で追加したアクションは自動的にボタン化される
    (QWidget.actions() でそのまま取り出せる)。
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._layout = FlowLayout(self)
        self._buttons: dict[QAction, QToolButton] = {}
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)

    def actionEvent(self, event) -> None:  # noqa: N802 (Qt のオーバーライド)
        """addAction()/removeAction() に合わせてボタンを増減する"""
        action = event.action()
        if event.type() == event.Type.ActionAdded and action not in self._buttons:
            button = QToolButton(self)
            button.setDefaultAction(action)
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
            button.setAutoRaise(True)
            self._buttons[action] = button
            self._layout.addWidget(button)
        elif event.type() == event.Type.ActionRemoved:
            button = self._buttons.pop(action, None)
            if button is not None:
                self._layout.removeWidget(button)
                button.deleteLater()
        super().actionEvent(event)

    def add_widget(self, widget: QWidget) -> None:
        """スライダーやスピンボックスなど、アクション以外の項目を追加する"""
        # QLayout.addWidget() 側で親が設定されるため setParent は不要
        self._layout.addWidget(widget)

    def add_separator(self) -> None:
        """項目のグループを区切る縦線を追加する"""
        # 立体的な彫り込み表現を避け、単色の細い縦線として描画する
        line = QFrame(self)
        line.setFrameShape(QFrame.Shape.NoFrame)
        line.setFixedSize(SEPARATOR_WIDTH, SEPARATOR_HEIGHT)
        line.setStyleSheet(f"background-color: {SEPARATOR_COLOR};")
        self._layout.addWidget(line)
