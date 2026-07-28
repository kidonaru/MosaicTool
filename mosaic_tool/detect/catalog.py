"""標準モデルのカタログ(セットアップ時に自動取得する .pt の定義)

いずれも HuggingFace の Anzhc/Anzhcs_YOLOs から認証なしで取得できる
セグメンテーションモデル。選定と推奨信頼度の根拠は docs/detection-models.md を参照。
"""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote

REPO_URL = "https://huggingface.co/Anzhc/Anzhcs_YOLOs"
# HuggingFace の直リンク。CDN へリダイレクトするため取得側は追従が要る
_DOWNLOAD_BASE = f"{REPO_URL}/resolve/main/"
LICENSE = "AGPL-3.0"


@dataclass(frozen=True)
class CatalogModel:
    """標準モデル 1 件の定義"""

    filename: str
    label: str        # 一覧に出す用途名(顔・目・髪)
    size_mb: float
    confidence: int   # 推奨する信頼度しきい値 (%)

    @property
    def url(self) -> str:
        """ダウンロード元(空白を含む名前があるため URL エンコードする)"""
        return _DOWNLOAD_BASE + quote(self.filename)


MODELS: tuple[CatalogModel, ...] = (
    CatalogModel("Anzhc Face seg 640 v4 y11n.pt", "顔", 5.7, 25),
    CatalogModel("Anzhc Eyes -seg-hd.pt", "目", 6.6, 40),
    # 髪の推奨値は未検証のため、全体の既定値と同じ 25% を置く
    CatalogModel("Anzhc HeadHair seg y8n.pt", "髪", 6.5, 25),
)


def find(filename: str) -> CatalogModel | None:
    """ファイル名でカタログを引く(カタログ外のモデルなら None)"""
    return next((m for m in MODELS if m.filename == filename), None)
