"""Info.plist の document types 生成の検証"""
import plistlib

import macos_bundle
from mosaic_tool.io_utils import IMAGE_EXTS


def test_document_types_cover_all_supported_extensions():
    types = macos_bundle.document_types()
    declared = {ext for t in types for ext in t["CFBundleTypeExtensions"]}
    assert declared == {ext.lstrip(".") for ext in IMAGE_EXTS}


def test_patch_info_plist_adds_document_types(tmp_path):
    app = tmp_path / "MosaicTool.app"
    contents = app / "Contents"
    contents.mkdir(parents=True)
    plist = contents / "Info.plist"
    plist.write_bytes(plistlib.dumps({"CFBundleName": "MosaicTool"}))

    macos_bundle.patch_info_plist(app)

    data = plistlib.loads(plist.read_bytes())
    assert data["CFBundleName"] == "MosaicTool"        # 既存のキーは残る
    assert data["CFBundleDocumentTypes"] == macos_bundle.document_types()
