"""標準モデルのカタログ(セットアップ時に自動取得する .pt の定義)

いずれも HuggingFace の Anzhc/Anzhcs_YOLOs から認証なしで取得できる
セグメンテーションモデル。推奨信頼度は実際に検出させて決めた値。
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
    label: str        # 一覧に出す用途名(顔・目)
    size_mb: float
    confidence: int   # 推奨する信頼度しきい値 (%)
    # 取得したファイルの SHA-256。.pt は読み込み時に pickle を展開するため、
    # 配布元が差し替わった場合に気づかず実行しないよう固定しておく
    sha256: str

    @property
    def url(self) -> str:
        """ダウンロード元(空白を含む名前があるため URL エンコードする)"""
        return _DOWNLOAD_BASE + quote(self.filename)


MODELS: tuple[CatalogModel, ...] = (
    CatalogModel(
        "Anzhc Face seg 640 v4 y11n.pt", "顔", 5.7, 25,
        "1e77ad7bd349babd8a4a90478bfc965348642b63a8d95d3b43ee13db42fd0a64",
    ),
    CatalogModel(
        "Anzhc Eyes -seg-hd.pt", "目", 6.6, 40,
        "6be1c13ca7a51c2425e278e07e7ae3d4c94ee125b874a0104a142f4f5a35a308",
    ),
)


def find(filename: str) -> CatalogModel | None:
    """ファイル名でカタログを引く(カタログ外のモデルなら None)"""
    return next((m for m in MODELS if m.filename == filename), None)
