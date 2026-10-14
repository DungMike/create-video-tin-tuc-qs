"""Dong bo hai file tu khoa voi MongoDB, de dot tim sau khong lap lai tu khoa cu.

    venv/Scripts/python -m src.tools.sync_keyword_files            # cap nhat ca hai file
    venv/Scripts/python -m src.tools.sync_keyword_files --dry-run  # chi bao cao, khong ghi

- keywords/video-keywords.txt  <-> ``search_keywords`` (harvest/prefetch video Pexels/Pixabay)
- keywords/image-keywords.txt  <-> ``image_search_cursors`` (thu vien clip tu anh)

Muc CHUA DUNG do nguoi viet: tu khoa da co trong DB (bat ky provider nao) hoac lap
lai trong muc bi go ra, thu tu va nhom "## ..." giu nguyen. Muc DA TIM duoc sinh lai
toan bo tu DB moi lan chay. Phan dau file (truoc muc CHUA DUNG) khong bi dong toi.
Chi DOC MongoDB, khong ghi gi vao DB.
"""

from __future__ import annotations

import argparse
import sys
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from src.db.keyword_repo import normalize_keyword

ROOT = Path(__file__).resolve().parents[2]
VIDEO_FILE = ROOT / "keywords" / "video-keywords.txt"
IMAGE_FILE = ROOT / "keywords" / "image-keywords.txt"

UNUSED_MARK = "== CHƯA DÙNG"
SEARCHED_MARK = "== ĐÃ TÌM"
UNUSED_HEADING = "== CHƯA DÙNG — copy các dòng từ khóa (bỏ dòng bắt đầu bằng #) dán vào ô tìm =="
SEARCHED_HEADING = "== ĐÃ TÌM — tự sinh từ MongoDB, đừng sửa tay (chạy lại lệnh sync để cập nhật) =="
EMPTY_NOTE = "# (đã dùng hết — thêm từ khóa mới vào đây rồi chạy lại lệnh sync)"

IMAGE_OPEN = "## Còn trang — tìm lại sẽ đi tiếp trang sau, vẫn ra ảnh mới"
IMAGE_DONE = "## Đã quét hết"

KEYWORD_WIDTH = 36
VIDEO_STATUS_NOTE = {"limited": "có giới hạn", "manual": "tìm tay", "partial": "quét dở"}


@dataclass
class Group:
    title: str | None  # dong "## ..."; None = cac dong nam truoc nhom dau tien
    lines: list[str] = field(default_factory=list)  # tu khoa + dong "# ..." chen giua


@dataclass
class Searched:
    keyword: str
    section: str
    details: list[str] = field(default_factory=list)


@dataclass
class Report:
    kept: int = 0
    used: list[str] = field(default_factory=list)
    duplicates: list[str] = field(default_factory=list)
    emptied: list[str] = field(default_factory=list)
    searched: int = 0


def _local_date(value) -> str:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return "?"
    if not isinstance(value, datetime):
        return "?"
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone().strftime("%Y-%m-%d")


def split_sections(text: str) -> tuple[str, list[str]]:
    """``(phan dau, cac dong cua muc CHUA DUNG)``. Thieu dong danh dau -> ValueError."""
    lines = text.splitlines()
    marks = [unicodedata.normalize("NFC", line.strip()) for line in lines]
    start = next((i for i, line in enumerate(marks) if line.startswith(UNUSED_MARK)), None)
    if start is None:
        raise ValueError(f'thieu dong "{UNUSED_MARK} ..."')
    end = next((i for i in range(start + 1, len(marks)) if marks[i].startswith(SEARCHED_MARK)), len(lines))
    return "\n".join(lines[:start]).rstrip(), lines[start + 1:end]


def parse_groups(lines: list[str]) -> list[Group]:
    groups = [Group(None)]
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        if line.startswith("##"):
            groups.append(Group(line))
        elif line != EMPTY_NOTE:
            groups[-1].lines.append(line)
    return groups


def prune(groups: list[Group], searched: dict[str, Searched]) -> tuple[list[Group], Report]:
    """Bo tu khoa da tim / lap lai. Nhom khong con tu khoa nao bi bo luon."""
    report = Report(searched=len(searched))
    seen: set[str] = set()
    kept_groups: list[Group] = []
    for group in groups:
        kept: list[str] = []
        had_keyword = has_keyword = False
        for line in group.lines:
            if line.startswith("#"):
                kept.append(line)
                continue
            had_keyword = True
            normalized = normalize_keyword(line)
            if normalized in searched:
                report.used.append(line)
            elif normalized in seen:
                report.duplicates.append(line)
            else:
                seen.add(normalized)
                kept.append(line)
                has_keyword = True
        if has_keyword:
            kept_groups.append(Group(group.title, kept))
        elif had_keyword and group.title:
            report.emptied.append(group.title)
    report.kept = len(seen)
    return kept_groups, report


def render(head: str, groups: list[Group], searched: dict[str, Searched], sections: list[str]) -> str:
    out = [head, ""] if head else []
    out += [UNUSED_HEADING, ""]
    for group in groups:
        if group.title:
            out.append(group.title)
        out += group.lines
        out.append("")
    if not groups:
        out += [EMPTY_NOTE, ""]
    out += [SEARCHED_HEADING, f"# {len(searched)} từ khóa"]
    for section in sections:
        entries = sorted(
            (entry for entry in searched.values() if entry.section == section),
            key=lambda entry: normalize_keyword(entry.keyword),
        )
        if not entries:
            continue
        out += ["", section]
        for entry in entries:
            out.append(f"{entry.keyword:<{KEYWORD_WIDTH}} | {'; '.join(entry.details)}".rstrip())
    return "\n".join(out) + "\n"


def sync_text(text: str, searched: dict[str, Searched], sections: list[str]) -> tuple[str, Report]:
    head, unused_lines = split_sections(text)
    groups, report = prune(parse_groups(unused_lines), searched)
    return render(head, groups, searched, sections), report


# ------------------------------------------------------------------- MongoDB
def video_searched() -> tuple[dict[str, Searched], list[str]]:
    """Moi tu khoa trong ``search_keywords`` (moi trang thai), gom theo ngay tim gan nhat."""
    from src.db import keyword_repo

    searched: dict[str, Searched] = {}
    # list_keywords sap xep moi nhat truoc: ban ghi dau tien quyet dinh ngay + cach viet.
    for item in keyword_repo.list_keywords(limit=10**7):
        normalized = item.get("normalized") or normalize_keyword(item.get("keyword"))
        if not normalized:
            continue
        entry = searched.get(normalized)
        if entry is None:
            entry = searched[normalized] = Searched(
                item.get("keyword") or normalized, f"## {_local_date(item.get('lastSearchedAt'))}"
            )
        detail = str(item.get("provider") or "?")
        if item.get("videosDownloaded"):
            detail += f" {item['videosDownloaded']} video"
        note = VIDEO_STATUS_NOTE.get(str(item.get("status") or ""))
        if note:
            detail += f" ({note})"
        entry.details.append(detail)
    for entry in searched.values():
        entry.details.sort()  # theo ten provider: file khong doi thu tu giua cac lan chay
    sections = sorted({entry.section for entry in searched.values()}, reverse=True)
    return searched, sections


def image_searched() -> tuple[dict[str, Searched], list[str]]:
    """Con tro goc trong ``image_search_cursors``: con trang hay da quet het."""
    from src.db import image_repo
    from src.utils.image_clip_search import QUERY_SIGNATURE

    searched: dict[str, Searched] = {}
    for doc in image_repo.list_cursors(limit=10**7):
        item = image_repo.serialize_cursor(doc, QUERY_SIGNATURE) or {}
        normalized = item.get("normalized") or normalize_keyword(item.get("keyword"))
        if not normalized:
            continue
        if not item.get("signatureCurrent"):
            state, still_open = "bộ lọc đã đổi, tìm lại từ trang 1", True
        elif item.get("exhausted"):
            state, still_open = "hết trang", False
        else:
            state, still_open = f"trang kế {item.get('nextPage')}", True
        detail = (
            f"{item.get('provider') or '?'} {state}, {item.get('photosAccepted') or 0} ảnh, "
            f"lần cuối {_local_date(item.get('lastSearchedAt'))}"
        )
        entry = searched.setdefault(normalized, Searched(item.get("keyword") or normalized, IMAGE_DONE))
        if still_open:
            entry.section = IMAGE_OPEN
        entry.details.append(detail)
    for entry in searched.values():
        entry.details.sort()
    return searched, [IMAGE_OPEN, IMAGE_DONE]


FILES = (
    ("video", VIDEO_FILE, video_searched),
    ("anh", IMAGE_FILE, image_searched),
)


def sync_file(path: Path, searched: dict[str, Searched], sections: list[str], dry_run: bool) -> tuple[Report, bool]:
    with open(path, "r", encoding="utf-8", newline="") as handle:
        text = handle.read()
    newline = "\r\n" if "\r\n" in text else "\n"
    updated, report = sync_text(text, searched, sections)
    changed = updated != text.replace("\r\n", "\n")
    if changed and not dry_run:
        with open(path, "w", encoding="utf-8", newline=newline) as handle:
            handle.write(updated)
    return report, changed


def _print_report(label: str, path: Path, report: Report, changed: bool, dry_run: bool) -> None:
    state = "khong doi" if not changed else ("SE DOI (dry-run)" if dry_run else "da ghi")
    print(f"[{label}] {path.relative_to(ROOT)} -- {state}")
    print(f"  chua dung: {report.kept} | da tim (DB): {report.searched}")
    if report.used:
        print(f"  go {len(report.used)} tu khoa da tim: {', '.join(report.used)}")
    if report.duplicates:
        print(f"  go {len(report.duplicates)} tu khoa lap lai: {', '.join(report.duplicates)}")
    if report.emptied:
        print(f"  nhom da dung het: {', '.join(report.emptied)}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Dong bo keywords/*.txt voi tu khoa da tim trong MongoDB.")
    parser.add_argument("--dry-run", action="store_true", help="chi bao cao, khong ghi file")
    args = parser.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

    from src.db import mongo

    if not mongo.is_configured():
        print("MONGODB_URI chua cau hinh trong .env -- khong co du lieu tu khoa da tim.")
        return 2
    if not mongo.is_available(force=True):
        print(mongo.unavailable_message())
        return 2

    status = 0
    for label, path, loader in FILES:
        if not path.exists():
            print(f"[{label}] khong thay {path} -- bo qua.")
            status = 1
            continue
        searched, sections = loader()
        try:
            report, changed = sync_file(path, searched, sections, args.dry_run)
        except ValueError as exc:
            print(f"[{label}] {path.name}: {exc} -- khong ghi.")
            status = 1
            continue
        _print_report(label, path, report, changed, args.dry_run)
    return status


if __name__ == "__main__":
    sys.exit(main())
