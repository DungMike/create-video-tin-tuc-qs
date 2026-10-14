"""INGEST cua pipeline: docx -> txt (khop DocxToTxt.ps1), quet folder, chuan hoa txt.

File *.expected.txt trong tests/fixtures/pipeline/ duoc sinh bang chinh ham
Get-DocxText cua "script convert docx to txt/DocxToTxt.ps1" (UTF-8 BOM, CRLF).
"""

import os

import pytest

from src.config import Config
from src.utils import pipeline_ingest as ingest

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "pipeline")


@pytest.mark.parametrize("name", ["basic", "textbox_fields"])
def test_docx_to_text_matches_powershell_byte_for_byte(name):
    expected = open(os.path.join(FIXTURES, f"{name}.expected.txt"), "rb").read()
    got = b"\xef\xbb\xbf" + ingest.docx_to_text(os.path.join(FIXTURES, f"{name}.docx")).encode("utf-8")
    assert got == expected


def test_docx_drops_fallback_duplicate_field_code_deleted_text_and_tab_stops():
    text = ingest.docx_to_text(os.path.join(FIXTURES, "textbox_fields.docx"))
    assert text.count("Chữ trong khung") == 1
    assert "PAGE" not in text and "đã xoá" not in text
    assert "Có tab stop" in text and "\tCó" not in text
    assert "Trường: 7" in text


def test_docx_xml_case_rules_follow_powershell():
    # -replace (khong phan biet hoa thuong) vs [regex]::Replace (co phan biet).
    xml = "<w:p><w:t>a</w:t><W:TAB/><w:t>b</w:t></W:P><w:delText>x</w:delText><W:DELTEXT>y</W:DELTEXT>&AMP;"
    assert ingest.docx_xml_to_text(xml) == "a\tb\r\ny&"


def test_invalid_docx_raises(tmp_path):
    bad = tmp_path / "bad.docx"
    bad.write_bytes(b"not a zip")
    with pytest.raises(ingest.IngestError) as info:
        ingest.docx_to_text(str(bad))
    assert info.value.code == "docx_invalid"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("xin chào".encode("utf-8"), "xin chào"),
        (b"\xef\xbb\xbf" + "có BOM".encode("utf-8"), "có BOM"),
        ("utf16 ภาษา".encode("utf-16"), "utf16 ภาษา"),
    ],
)
def test_read_text_file_encodings(tmp_path, raw, expected):
    path = tmp_path / "a.txt"
    path.write_bytes(raw)
    assert ingest.read_text_file(str(path)) == expected


def test_read_text_file_rejects_non_utf8(tmp_path):
    path = tmp_path / "a.txt"
    path.write_bytes("café".encode("latin-1"))  # khong phai UTF-8
    with pytest.raises(ingest.IngestError) as info:
        ingest.read_text_file(str(path))
    assert info.value.code == "encoding_unsupported"


def test_prepare_item_text_writes_utf8_bom_and_keeps_source(tmp_path):
    source = tmp_path / "Tập 1.docx"
    source.write_bytes(open(os.path.join(FIXTURES, "basic.docx"), "rb").read())
    dest = tmp_path / "items" / "it-01" / "tap-1.txt"
    info = ingest.prepare_item_text(str(source), str(dest))
    data = dest.read_bytes()
    assert data.startswith(b"\xef\xbb\xbf")
    assert data == open(os.path.join(FIXTURES, "basic.expected.txt"), "rb").read()
    assert source.exists()  # khong xoa docx nhu DocxToTxt.ps1
    assert info["kind"] == "docx" and len(info["sha256"]) == 64


def test_prepare_item_text_blank_line_option(tmp_path):
    source = tmp_path / "a.txt"
    source.write_text("dòng 1\ndòng 2\n\n\ndòng 3\n", encoding="utf-8")
    dest = tmp_path / "out.txt"
    ingest.prepare_item_text(str(source), str(dest), blank_line_between_paragraphs=True)
    assert dest.read_bytes().decode("utf-8-sig") == "dòng 1\r\n\r\ndòng 2\r\n\r\ndòng 3"


def test_prepare_item_text_warns_long_paragraphs_and_rejects_empty(tmp_path):
    long_src = tmp_path / "long.txt"
    long_src.write_text("a" * 600 + "\n\nngắn", encoding="utf-8")
    info = ingest.prepare_item_text(str(long_src), str(tmp_path / "o1.txt"))
    assert info["paragraphs"] == 2 and info["warnings"]

    empty = tmp_path / "empty.txt"
    empty.write_text("  \n ", encoding="utf-8")
    with pytest.raises(ingest.IngestError) as info_err:
        ingest.prepare_item_text(str(empty), str(tmp_path / "o2.txt"))
    assert info_err.value.code == "empty_text"


def test_prepare_item_text_size_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(Config, "PIPELINE_MAX_TEXT_BYTES", 20)
    source = tmp_path / "a.txt"
    source.write_text("x" * 50, encoding="utf-8")
    with pytest.raises(ingest.IngestError) as info:
        ingest.prepare_item_text(str(source), str(tmp_path / "o.txt"))
    assert info.value.code == "text_too_large"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("Tập 01 – Đêm trăng", "tap-01-dem-trang"),
        ("ตอนที่ 1", "1"),
        ("ตอนที่", ""),
        ("  ---  ", ""),
        ("a" * 80, "a" * 48),
    ],
)
def test_slugify(value, expected):
    assert ingest.slugify(value) == expected


def test_scan_folder_natural_sort_duplicates_and_temp_files(tmp_path):
    (tmp_path / "Tap 10.txt").write_text("x", encoding="utf-8")
    (tmp_path / "Tap 2.docx").write_bytes(open(os.path.join(FIXTURES, "basic.docx"), "rb").read())
    (tmp_path / "Tap 2.txt").write_text("x", encoding="utf-8")
    (tmp_path / "~$Tap 3.docx").write_bytes(b"lock")
    (tmp_path / "ghi chu.md").write_text("x", encoding="utf-8")
    sub = tmp_path / "phan 2"
    sub.mkdir()
    (sub / "Tap 11.txt").write_text("x", encoding="utf-8")

    flat = ingest.scan_folder(str(tmp_path))
    names = [f["name"] for f in flat["files"]]
    assert names == ["Tap 2.docx", "Tap 2.txt", "Tap 10.txt"]
    docx_entry = flat["files"][0]
    assert docx_entry["valid"] is False and "Tap 2.txt" in docx_entry["warning"]
    assert flat["warnings"]

    deep = ingest.scan_folder(str(tmp_path), recursive=True)
    # Sap xep theo duong dan tuong doi: thu muc con "phan 2" dung truoc "Tap ..."
    assert [f["name"] for f in deep["files"]] == ["Tap 11.txt", "Tap 2.docx", "Tap 2.txt", "Tap 10.txt"]


def test_scan_folder_missing(tmp_path):
    with pytest.raises(ingest.IngestError) as info:
        ingest.scan_folder(str(tmp_path / "khong-co"))
    assert info.value.code == "folder_not_found"


def test_safe_upload_name_keeps_unicode():
    assert ingest.safe_upload_name("..\\x/Tập: 1?.docx") == "Tập_ 1_.docx"
