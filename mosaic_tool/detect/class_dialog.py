"""クラス選択ダイアログ: モデル 1 件の検出対象クラスをチェックボックスで選ぶ"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

INITIAL_WIDTH = 360
INITIAL_HEIGHT = 400
NO_CLASS_TEXT = "このモデルからクラス情報を取得できませんでした。"


class ClassSelectDialog(QDialog):
    """検出対象クラスの選択

    selected が空なら全クラスを対象とみなして全チェックにする。
    全解除のままでは閉じられない(空の保存は「未設定 = 全部」と区別がつかない)。
    """

    def __init__(
        self,
        model_name: str,
        class_names: list[str],
        selected: list[str],
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("検出クラス")
        self.resize(INITIAL_WIDTH, INITIAL_HEIGHT)
        self._checks: list[QCheckBox] = []

        layout = QVBoxLayout(self)
        self._title_label = QLabel(f"{model_name} の検出対象クラス")
        self._title_label.setWordWrap(True)
        layout.addWidget(self._title_label)

        # 保存済みの名前が 1 つも残っていなければ全クラスへ戻す
        wanted = {n for n in selected if n in class_names}
        if not wanted:
            wanted = set(class_names)

        inner = QWidget()
        inner_layout = QVBoxLayout(inner)
        for name in class_names:
            check = QCheckBox(name)
            check.setChecked(name in wanted)
            check.toggled.connect(self._update_ok_enabled)
            inner_layout.addWidget(check)
            self._checks.append(check)
        if not class_names:
            inner_layout.addWidget(QLabel(NO_CLASS_TEXT))
        inner_layout.addStretch()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(inner)
        layout.addWidget(scroll, stretch=1)

        tools = QHBoxLayout()
        select_all = QPushButton("全選択")
        select_all.clicked.connect(self._on_select_all)
        tools.addWidget(select_all)
        clear_all = QPushButton("全解除")
        clear_all.clicked.connect(self._on_clear_all)
        tools.addWidget(clear_all)
        tools.addStretch()
        layout.addLayout(tools)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self._ok_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        layout.addWidget(buttons)
        self._update_ok_enabled()

    def selected_classes(self) -> list[str]:
        """チェックされたクラス名(モデルのクラス順)"""
        return [c.text() for c in self._checks if c.isChecked()]

    def _update_ok_enabled(self) -> None:
        # 1 つも選ばれていない状態は保存させない
        self._ok_button.setEnabled(bool(self.selected_classes()))

    def _on_select_all(self) -> None:
        for check in self._checks:
            check.setChecked(True)

    def _on_clear_all(self) -> None:
        for check in self._checks:
            check.setChecked(False)
