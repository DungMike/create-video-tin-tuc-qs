"""Kho tu khoa cho luong tu dong tim nguon video (src/utils/story_auto_source.py).

Hai nguon, theo thu tu uu tien:

1. ``keywords/video-keywords.txt`` muc CHUA DUNG -- nguoi dung tu viet (theo kenh).
2. Bo sinh tu dong: chu de (thien nhien, cay la, hoa, bien, nui, song ho, nong thon,
   pho xa) ghep voi dia danh toan cau. Moi dia danh khai bao "dac diem" (co bien,
   co nui tuyet, co ruong bac thang...) va chu de chi ghep voi dia danh co dac diem
   no can, nen khong sinh cap vo nghia kieu "desert norway".

Ca hai tra ve tu khoa tieng Anh (Pexels/Pixabay cho nhieu ket qua hon han). Thu tu bo
sinh xao bang seed co dinh: on dinh giua cac lan chay, va lan sau chi viec bo qua tu da
dung trong DB (search_keywords / video_search_cursors).
"""

from __future__ import annotations

import random
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VIDEO_KEYWORD_FILE = ROOT / "keywords" / "video-keywords.txt"

_SEED = 20261021

# (chu de, dac diem dia danh can co). None = hop moi dia danh.
THEMES: tuple[tuple[str, str | None], ...] = (
    # Rung, cay la, hoa
    ("forest", "forest"),
    ("forest aerial", "forest"),
    ("misty forest", "forest"),
    ("forest stream", "forest"),
    ("rainforest", "tropical"),
    ("jungle river", "tropical"),
    ("palm trees", "tropical"),
    ("autumn leaves", "autumn"),
    ("autumn forest", "autumn"),
    ("cherry blossom", "blossom"),
    ("flower field", "flowers"),
    ("wildflowers", "flowers"),
    ("tea plantation", "tea"),
    ("green hills", "countryside"),
    ("meadow", "countryside"),
    # Nui
    ("mountains", "mountain"),
    ("mountain aerial", "mountain"),
    ("mountain fog", "mountain"),
    ("mountain valley", "mountain"),
    ("snow mountains", "snow"),
    ("winter landscape", "snow"),
    ("desert", "desert"),
    ("savanna", "savanna"),
    # Song, ho, thac
    ("waterfall", "waterfall"),
    ("river", "river"),
    ("river aerial", "river"),
    ("lake", "lake"),
    ("lake reflection", "lake"),
    # Bien, dao
    ("beach", "coast"),
    ("ocean waves", "coast"),
    ("coastline aerial", "coast"),
    ("sunset beach", "coast"),
    ("fishing boats", "coast"),
    ("sea cliffs", "cliffs"),
    ("island aerial", "island"),
    ("harbor", "harbor"),
    # Nong thon
    ("countryside", "countryside"),
    ("village", "countryside"),
    ("farm", "countryside"),
    ("farmers working", "countryside"),
    ("rice fields", "rice"),
    ("rice terraces", "terraces"),
    # Pho xa, doi song thanh pho
    ("city street", "city"),
    ("city night", "city"),
    ("city aerial", "city"),
    ("city traffic", "city"),
    ("city timelapse", "city"),
    ("people walking street", "city"),
    ("street market", "market"),
    ("night market", "nightmarket"),
    ("old town", "oldtown"),
    ("cafe street", "cafe"),
    # Bau troi, thoi tiet
    ("sunrise", "sky"),
    ("sunset", "sky"),
    ("clouds timelapse", "sky"),
    ("rain", "rain"),
)

# Dia danh -> dac diem. Quoc gia co "sky" (binh minh/hoang hon dep, hay duoc quay).
PLACES: dict[str, str] = {
    # Dong Nam A
    "vietnam": "forest tropical mountain waterfall river lake coast island harbor countryside rice terraces "
               "city market nightmarket oldtown cafe rain tea flowers sky",
    "thailand": "forest tropical mountain waterfall river lake coast cliffs island harbor countryside rice "
                "city market nightmarket oldtown cafe rain flowers sky",
    "laos": "forest tropical mountain waterfall river countryside rice market nightmarket oldtown sky",
    "cambodia": "tropical river lake countryside rice market oldtown sky",
    "indonesia": "forest tropical mountain waterfall coast island countryside rice terraces market city sky",
    "bali": "tropical waterfall coast cliffs countryside rice terraces market sky",
    "philippines": "tropical coast island waterfall countryside rice terraces city market sky",
    "malaysia": "tropical forest coast island city market nightmarket tea",
    "myanmar": "river countryside rice market oldtown sky",
    # Dong A, Nam A
    "japan": "forest mountain snow autumn blossom river lake coast harbor countryside rice city market "
             "nightmarket oldtown cafe rain tea flowers sky",
    "korea": "mountain autumn blossom coast harbor countryside city market nightmarket cafe sky",
    "china": "mountain river lake countryside rice terraces city market oldtown tea sky",
    "taiwan": "mountain coast city nightmarket tea",
    "nepal": "mountain snow countryside terraces oldtown sky",
    "india": "mountain river coast countryside city market oldtown tea sky",
    "sri lanka": "tropical coast waterfall countryside tea",
    # Trung Dong, chau Phi
    "turkey": "mountain coast countryside city market oldtown sky",
    "morocco": "desert mountain city market oldtown sky",
    "egypt": "desert river city market sky",
    "kenya": "savanna countryside sky",
    "tanzania": "savanna coast island sky",
    "south africa": "mountain coast city savanna sky",
    # Chau Au
    "italy": "mountain lake coast cliffs harbor countryside city oldtown market cafe flowers sky",
    "france": "mountain lake countryside city oldtown market cafe flowers coast sky",
    "spain": "coast city oldtown market cafe countryside sky",
    "portugal": "coast cliffs harbor city oldtown cafe",
    "switzerland": "mountain snow lake countryside oldtown",
    "austria": "mountain snow lake countryside oldtown",
    "norway": "mountain snow lake coast cliffs waterfall forest harbor",
    "iceland": "waterfall snow coast cliffs mountain",
    "scotland": "mountain lake coast countryside oldtown",
    "ireland": "coast cliffs countryside",
    "england": "countryside city oldtown coast autumn",
    "netherlands": "countryside flowers city oldtown",
    "germany": "forest autumn countryside city oldtown market",
    "greece": "coast island cliffs harbor oldtown sky",
    "croatia": "coast island harbor oldtown waterfall lake",
    # Chau My, chau Dai Duong
    "canada": "forest mountain snow lake autumn river city",
    "usa": "forest mountain desert coast city autumn lake river sky",
    "mexico": "desert coast city market oldtown sky",
    "peru": "mountain countryside terraces oldtown market",
    "brazil": "tropical forest coast city waterfall sky",
    "argentina": "mountain snow lake city",
    "chile": "mountain desert lake coast",
    "new zealand": "mountain lake coast forest countryside waterfall",
    "australia": "coast forest desert city sky",
    # Thanh pho (doi song pho xa)
    "hanoi": "city market nightmarket oldtown cafe",
    "ho chi minh city": "city market nightmarket cafe",
    "tokyo": "city market nightmarket cafe",
    "kyoto": "oldtown autumn blossom",
    "seoul": "city market nightmarket cafe",
    "hong kong": "city harbor nightmarket",
    "singapore": "city harbor nightmarket",
    "taipei": "city nightmarket",
    "shanghai": "city",
    "istanbul": "city market oldtown harbor",
    "paris": "city cafe oldtown",
    "london": "city oldtown",
    "new york": "city cafe",
    "rome": "city oldtown cafe",
    "barcelona": "city oldtown market",
    "amsterdam": "city oldtown",
    "venice": "oldtown harbor",
    "lisbon": "city oldtown harbor",
}


def generated_keywords() -> list[str]:
    """Moi cap (chu de, dia danh) hop le, xao bang seed co dinh."""
    keywords: list[str] = []
    for place, features in PLACES.items():
        traits = set(features.split())
        for theme, need in THEMES:
            if need is None or need in traits:
                keywords.append(f"{theme} {place}")
    random.Random(_SEED).shuffle(keywords)
    return keywords


def file_keywords(path: Path | None = None) -> list[str]:
    """Tu khoa muc CHUA DUNG cua ``keywords/video-keywords.txt`` (rong neu thieu file).

    Xen ke cac nhom "## ..." (tu 1 cua moi nhom, roi tu 2...) de moi luot chay trai
    deu nhieu chu de thay vi vet het mot nhom.
    """
    from src.tools.sync_keyword_files import parse_groups, split_sections

    target = path or VIDEO_KEYWORD_FILE
    try:
        text = target.read_text(encoding="utf-8")
        _head, lines = split_sections(text)
    except (OSError, ValueError):
        return []
    groups = [
        [line for line in group.lines if not line.startswith("#")]
        for group in parse_groups(lines)
    ]
    return interleave(*groups)


def interleave(*lists: list[str]) -> list[str]:
    """``[a1, b1, c1, a2, b2, ...]`` -- lay lan luot tu moi danh sach."""
    out: list[str] = []
    for index in range(max((len(items) for items in lists), default=0)):
        out.extend(items[index] for items in lists if index < len(items))
    return out
