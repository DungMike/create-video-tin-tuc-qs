"""Buoc INGEST cua pipeline: quet folder, doc kich ban .docx/.txt, chuan hoa txt.

``docx_to_text`` la ban Python cua ``script convert docx to txt/DocxToTxt.ps1``,
khop tung buoc (xem docs/integration/04-thiet-ke-pipeline.md §5). Khac duy nhat:
KHONG xoa file docx goc. Luu y PowerShell: dong dung ``[regex]::Replace`` phan biet
hoa thuong, dong dung ``-replace`` thi khong.

Txt dua cho DGT ghi UTF-8 co BOM (DGT chap nhan BOM, chi tu choi khi khong phai UTF-8).
"""

from __future__ import annotations

import hashlib
import os
import re
import unicodedata
import zipfile

from src.config import Config

TEXT_EXTENSIONS = {".docx", ".txt"}
# Dau vao "co san audio + srt": bo qua INGEST/TTS. Thu tu = uu tien khi trung ten.
AUDIO_EXTENSIONS = (".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg")
SRT_EXTENSIONS = {".srt"}
DGT_CHUNK_LIMIT = 499  # DGT tu cat khoi dai hon ~500 ky tu


class IngestError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


# --- docx -> text (khop DocxToTxt.ps1) --------------------------------------------

_CASE_SENSITIVE_STRIPS = [
    r"(?s)<mc:Fallback[^>]*>.*?</mc:Fallback>",
    r"(?s)<w:instrText[^>]*>.*?</w:instrText>",
    r"(?s)<w:delText[^>]*>.*?</w:delText>",
    r"(?s)<w:tabs(\s[^>]*)?>.*?</w:tabs>",
]

_STRUCTURE_REPLACEMENTS = [
    (r"</w:p>", "\r\n"),
    (r"<w:p(\s[^>]*)?/>", "\r\n"),
    (r"<w:br(\s[^>]*)?/?>", "\r\n"),
    (r"<w:cr(\s[^>]*)?/?>", "\r\n"),
    (r"<w:tab(\s[^>]*)?/?>", "\t"),
    (r"<w:noBreakHyphen(\s[^>]*)?/?>", "-"),
]

_ENTITIES = [("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"'), ("&apos;", "'"), ("&amp;", "&")]

# Tap ky tu ma String.Trim()/TrimEnd() cua .NET coi la khoang trang (Char.IsWhiteSpace).
# str.strip() cua Python khac o vai ky tu (vd U+001C..U+001F), nen dung tap rieng.
_NET_WHITESPACE = (
    "\t\n\x0b\x0c\r \x85\xa0        "
    "        　"
)


def docx_xml_to_text(xml: str) -> str:
    for pattern in _CASE_SENSITIVE_STRIPS:
        xml = re.sub(pattern, "", xml)
    for pattern, replacement in _STRUCTURE_REPLACEMENTS:
        xml = re.sub(pattern, replacement, xml, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", xml, flags=re.IGNORECASE)
    text = re.sub(r"&#(\d+);", lambda m: chr(int(m.group(1))), text)
    text = re.sub(r"&#x([0-9A-Fa-f]+);", lambda m: chr(int(m.group(1), 16)), text)
    for entity, char in _ENTITIES:  # &amp; phai giai ma cuoi cung
        text = re.sub(re.escape(entity), lambda _m, c=char: c, text, flags=re.IGNORECASE)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return "\r\n".join(line.rstrip(_NET_WHITESPACE) for line in text.split("\n")).strip(_NET_WHITESPACE)


def docx_to_text(path: str) -> str:
    try:
        with zipfile.ZipFile(path) as archive:
            try:
                raw = archive.read("word/document.xml")
            except KeyError as exc:
                raise IngestError("docx_invalid", "Không tìm thấy word/document.xml — file không phải .docx hợp lệ.") from exc
    except zipfile.BadZipFile as exc:
        raise IngestError("docx_invalid", "File .docx hỏng hoặc không phải .docx.") from exc
    # StreamReader(UTF8) cua PowerShell tu bo BOM neu co.
    return docx_xml_to_text(raw.decode("utf-8-sig"))


# --- txt ------------------------------------------------------------------------

def read_text_file(path: str) -> str:
    with open(path, "rb") as file_obj:
        raw = file_obj.read()
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        try:
            return raw.decode("utf-16")
        except UnicodeDecodeError as exc:
            raise IngestError("encoding_unsupported", "File UTF-16 bị lỗi mã hoá.") from exc
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise IngestError(
            "encoding_unsupported",
            "File .txt không phải UTF-8. Hãy lưu lại file dưới dạng UTF-8 rồi chạy lại.",
        ) from exc


def insert_blank_lines(text: str) -> str:
    """Moi dong khong rong thanh 1 doan rieng (DGT chi tach doan TTS o dong trong)."""
    lines = [line.rstrip() for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    return "\r\n\r\n".join(line for line in lines if line.strip())


def paragraph_stats(text: str) -> dict:
    """Thong ke theo cach DGT chia: khoi ngan cach boi dong trong."""
    blocks = [b for b in re.split(r"\n\s*\n", text.replace("\r\n", "\n")) if b.strip()]
    long_blocks = [len(b) for b in blocks if len(b.strip()) > DGT_CHUNK_LIMIT]
    return {"blocks": len(blocks), "longBlocks": len(long_blocks), "chars": len(text)}


def write_dgt_text(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as file_obj:
        file_obj.write(b"\xef\xbb\xbf" + text.encode("utf-8"))


# --- ten / slug -----------------------------------------------------------------

def slugify(value: str, max_len: int = 48) -> str:
    value = value.replace("đ", "d").replace("Đ", "D")
    value = unicodedata.normalize("NFD", value)
    value = "".join(ch for ch in value if unicodedata.category(ch) != "Mn")
    value = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return value[:max_len].strip("-")


def natural_key(name: str) -> list:
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", name)]


def safe_upload_name(name: str) -> str:
    """Giu ten Unicode, chi bo ky tu cam tren Windows va phan thu muc."""
    name = os.path.basename(name.replace("\\", "/"))
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    return name or "file"


def sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as file_obj:
        for chunk in iter(lambda: file_obj.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


# --- quet folder -----------------------------------------------------------------

def _list_files(root: str, recursive: bool, extensions) -> list[str]:
    found: list[str] = []
    if recursive:
        for current, dirs, files in os.walk(root, followlinks=False):
            dirs.sort(key=natural_key)
            found.extend(os.path.join(current, name) for name in files)
    else:
        found = [os.path.join(root, name) for name in os.listdir(root)]
    return [
        file_path for file_path in found
        if os.path.splitext(file_path)[1].lower() in extensions
        and os.path.isfile(file_path) and not os.path.islink(file_path)
        and not os.path.basename(file_path).startswith("~$")
    ]


def scan_folder(path: str, recursive: bool = False, mode: str = "script") -> dict:
    """Liet ke .docx/.txt trong folder (chi doc). Trung ten goc thi uu tien .txt.

    ``mode="media"``: liet ke cap audio + .srt cung ten (xem ``scan_media_pairs``).
    """
    root = os.path.abspath(path)
    if not os.path.isdir(root):
        raise IngestError("folder_not_found", f"Không tìm thấy thư mục: {path}")
    if mode == "media":
        return {"path": root, "recursive": recursive, "mode": "media", **scan_media_pairs(root, recursive)}

    entries = [describe_source(file_path) for file_path in _list_files(root, recursive, TEXT_EXTENSIONS)]

    by_stem: dict[tuple[str, str], list[dict]] = {}
    for entry in entries:
        key = (os.path.dirname(entry["path"]).lower(), entry["stem"].lower())
        by_stem.setdefault(key, []).append(entry)
    warnings: list[str] = []
    for group in by_stem.values():
        if len(group) > 1:
            txt = next((e for e in group if e["kind"] == "txt"), None)
            for entry in group:
                if txt and entry is not txt:
                    entry["valid"] = False
                    entry["warning"] = f"Trùng tên với {os.path.basename(txt['path'])}, dùng file .txt"
            warnings.append(f"Trùng tên: {', '.join(os.path.basename(e['path']) for e in group)}")

    entries.sort(key=lambda e: natural_key(os.path.relpath(e["path"], root)))
    return {"path": root, "recursive": recursive, "files": entries, "warnings": warnings}


def scan_media_pairs(root: str, recursive: bool = False) -> dict:
    """Ghep audio voi .srt cung ten (cung thu muc, khong phan biet hoa thuong).

    Audio thieu .srt van liet ke (valid=False) de nguoi dung thay; .srt thieu audio chi
    vao ``warnings``. Trung ten nhieu audio: lay theo thu tu ``AUDIO_EXTENSIONS``.
    """
    groups: dict[tuple[str, str], dict[str, list[str]]] = {}
    for file_path in _list_files(root, recursive, {*AUDIO_EXTENSIONS, *SRT_EXTENSIONS}):
        stem, ext = os.path.splitext(os.path.basename(file_path))
        group = groups.setdefault((os.path.dirname(file_path).lower(), stem.lower()), {"audio": [], "srt": []})
        group["srt" if ext.lower() in SRT_EXTENSIONS else "audio"].append(file_path)

    entries: list[dict] = []
    warnings: list[str] = []
    for group in groups.values():
        srt = sorted(group["srt"])[0] if group["srt"] else None
        audios = sorted(group["audio"], key=lambda p: AUDIO_EXTENSIONS.index(os.path.splitext(p)[1].lower()))
        if not audios:
            warnings.append(f"{os.path.basename(srt)}: không có file audio cùng tên")
            continue
        for position, audio in enumerate(audios):
            entry = describe_media(audio, srt)
            if position:
                entry["valid"] = False
                entry["warning"] = f"Trùng tên với {os.path.basename(audios[0])}, dùng file đó"
            entries.append(entry)

    entries.sort(key=lambda e: natural_key(os.path.relpath(e["path"], root)))
    return {"files": entries, "warnings": warnings}


def describe_media(audio_path: str, srt_path: str | None) -> dict:
    name = os.path.basename(audio_path)
    stem, ext = os.path.splitext(name)
    entry = {
        "path": os.path.abspath(audio_path),
        "name": name,
        "stem": stem,
        "kind": ext.lower().lstrip("."),
        "size": os.path.getsize(audio_path),
        "srtPath": os.path.abspath(srt_path) if srt_path else None,
        "srtName": os.path.basename(srt_path) if srt_path else None,
        "valid": bool(srt_path),
    }
    if not srt_path:
        entry["warning"] = "Thiếu file .srt cùng tên"
    return entry


def describe_source(file_path: str) -> dict:
    name = os.path.basename(file_path)
    stem, ext = os.path.splitext(name)
    size = os.path.getsize(file_path)
    entry = {
        "path": os.path.abspath(file_path),
        "name": name,
        "stem": stem,
        "kind": ext.lower().lstrip("."),
        "size": size,
        "valid": True,
    }
    if size > Config.PIPELINE_MAX_TEXT_BYTES and ext.lower() == ".txt":
        entry["valid"] = False
        entry["warning"] = "File lớn hơn giới hạn 10 MiB của app TTS"
    return entry


# --- chuan bi txt cho 1 item ------------------------------------------------------

def prepare_item_text(source_path: str, dest_txt: str, *, blank_line_between_paragraphs: bool = False) -> dict:
    """Doc nguon (.docx/.txt), ghi txt chuan cho DGT. Tra ve thong tin + canh bao."""
    ext = os.path.splitext(source_path)[1].lower()
    if ext not in TEXT_EXTENSIONS:
        raise IngestError("unsupported_file", "Chỉ nhận file .docx hoặc .txt.")
    if not os.path.isfile(source_path):
        raise IngestError("source_not_found", f"Không tìm thấy file: {source_path}")

    text = docx_to_text(source_path) if ext == ".docx" else read_text_file(source_path)
    if not text.strip():
        raise IngestError("empty_text", "Kịch bản rỗng.")
    if blank_line_between_paragraphs:
        text = insert_blank_lines(text)

    encoded_size = len(text.encode("utf-8")) + 3
    if encoded_size > Config.PIPELINE_MAX_TEXT_BYTES:
        raise IngestError("text_too_large", "Kịch bản lớn hơn giới hạn 10 MiB của app TTS.")

    write_dgt_text(dest_txt, text)
    stats = paragraph_stats(text)
    warnings = []
    if stats["longBlocks"]:
        warnings.append(f"{stats['longBlocks']} đoạn dài hơn {DGT_CHUNK_LIMIT} ký tự — app TTS sẽ tự cắt")
    return {
        "kind": ext.lstrip("."),
        "sha256": sha256_file(source_path),
        "chars": stats["chars"],
        "paragraphs": stats["blocks"],
        "warnings": warnings,
    }
