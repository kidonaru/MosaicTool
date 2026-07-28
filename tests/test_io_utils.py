from pathlib import Path

from PIL import Image

from mosaic_tool import io_utils


def test_mc_file_path():
    # ファイル単体: 同じ場所に 名前_mc.拡張子
    assert io_utils.mc_file_path(Path("C:/a/photo.png")) == Path("C:/a/photo_mc.png")
    assert io_utils.mc_file_path(Path("C:/a/photo.test.jpg")) == Path("C:/a/photo.test_mc.jpg")


def test_mc_folder_path():
    assert io_utils.mc_folder_path(Path("C:/a/shots")) == Path("C:/a/shots_mc")


def test_is_image_file():
    assert io_utils.is_image_file(Path("a.PNG"))
    assert io_utils.is_image_file(Path("a.webp"))
    assert not io_utils.is_image_file(Path("a.txt"))


def test_list_images(tmp_path):
    (tmp_path / "b.png").write_bytes(b"x")
    (tmp_path / "a.jpg").write_bytes(b"x")
    (tmp_path / "c.txt").write_bytes(b"x")
    (tmp_path / "sub").mkdir()
    # 対応画像のみを名前順で列挙する
    assert [p.name for p in io_utils.list_images(tmp_path)] == ["a.jpg", "b.png"]


def test_load_save_roundtrip_png(tmp_path):
    src = tmp_path / "in.png"
    Image.new("RGBA", (10, 10), (255, 0, 0, 128)).save(src)
    img = io_utils.load_image(src)
    assert img.size == (10, 10)
    dest = tmp_path / "out" / "in_mc.png"
    io_utils.save_image(img, dest)  # 親フォルダは自動作成される
    assert Image.open(dest).mode == "RGBA"


def test_save_keeps_meta_by_default(tmp_path):
    img = Image.new("RGB", (10, 10))
    img.info["icc_profile"] = b"fake-icc"
    dest = tmp_path / "keep.png"
    io_utils.save_image(img, dest)
    assert Image.open(dest).info.get("icc_profile") == b"fake-icc"


def test_save_strips_meta(tmp_path):
    # keep_meta=False なら info 経由で引き継がれるメタ情報も残さない
    img = Image.new("RGB", (10, 10))
    img.info["icc_profile"] = b"fake-icc"
    img.info["Comment"] = "secret"
    dest = tmp_path / "strip.png"
    io_utils.save_image(img, dest, keep_meta=False)
    saved = Image.open(dest).info
    assert "icc_profile" not in saved
    assert "Comment" not in saved
    # 元画像の info は壊さない
    assert img.info["icc_profile"] == b"fake-icc"


def test_save_strips_meta_keeps_transparency(tmp_path):
    # 透過は見た目に影響するためメタ削除でも残す
    img = Image.new("P", (10, 10))
    img.info["transparency"] = 0
    dest = tmp_path / "trans.png"
    io_utils.save_image(img, dest, keep_meta=False)
    assert Image.open(dest).info.get("transparency") == 0


def test_save_jpg_converts_rgba(tmp_path):
    # JPEG はアルファ非対応のため RGB に変換して保存する
    img = Image.new("RGBA", (10, 10), (255, 0, 0, 128))
    dest = tmp_path / "x.jpg"
    io_utils.save_image(img, dest)
    assert Image.open(dest).mode == "RGB"
