"""Chia mot tap asset cho N video trong mot batch (bo bai xao).

Dung chung cho anh decor (khung TV), song am va CTA overlay: moi loai tu loc
danh sach "dung duoc" cua minh roi goi :func:`deal_rotation` de chia.
"""

import random


def deal_rotation(ids: list[str], count: int) -> list[str]:
    """Gan mot asset cho moi video trong ``count`` video, xao khong lap lai.

    Bo bai duoc xao, chia het roi xao lai — nen mot batch 50 video voi 7 asset
    dung du ca 7 trong moi cum 7 video, thu tu khac nhau moi lan, va khong asset
    nao bi bo doi nhu cach boc ngau nhien doc lap co the gay ra.
    """
    if not ids or count <= 0:
        return []

    assignments: list[str] = []
    deck: list[str] = []
    previous: str | None = None
    while len(assignments) < count:
        if not deck:
            deck = list(ids)
            random.shuffle(deck)
            # Tranh lap o moi noi: xao lai co the dua dung la vua dung len dau,
            # nhin nhu "vong xoay bi hong".
            if len(deck) > 1 and deck[0] == previous:
                deck.append(deck.pop(0))
        previous = deck.pop(0)
        assignments.append(previous)
    return assignments
