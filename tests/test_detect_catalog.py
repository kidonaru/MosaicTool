"""標準モデルカタログの定義の検証"""
from urllib.parse import unquote

from mosaic_tool.detect import catalog


def test_filenames_are_unique():
    names = [m.filename for m in catalog.MODELS]
    assert len(names) == len(set(names))


def test_all_filenames_end_with_pt():
    assert all(m.filename.endswith(".pt") for m in catalog.MODELS)


def test_urls_point_at_the_repository_with_encoded_filename():
    for model in catalog.MODELS:
        assert model.url.startswith(
            "https://huggingface.co/Anzhc/Anzhcs_YOLOs/resolve/main/"
        )
        # URL エンコードを戻すとファイル名に一致する(空白を含む名前があるため)
        assert unquote(model.url.rsplit("/", 1)[1]) == model.filename


def test_urls_are_percent_encoded():
    # 空白を含む名前がそのまま URL に入っていないこと
    assert all(" " not in model.url for model in catalog.MODELS)


def test_confidence_is_within_percentage_range():
    assert all(1 <= m.confidence <= 100 for m in catalog.MODELS)


def test_every_model_has_a_label_and_size():
    assert all(m.label for m in catalog.MODELS)
    assert all(m.size_mb > 0 for m in catalog.MODELS)


def test_find_returns_the_matching_model():
    assert catalog.find("Anzhc Eyes -seg-hd.pt").label == "目"


def test_find_returns_none_for_unknown_filename():
    assert catalog.find("unknown.pt") is None
