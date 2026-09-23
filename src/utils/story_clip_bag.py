"""Shared shuffle-bag for batch story-video clip selection.

Within one batch, every video draws clips from a single shuffled deck without
replacement; the deck is reshuffled and refilled only once it runs out. A clip
can therefore repeat inside a batch only after the whole pool has been used at
least once, capping per-clip reuse at ceil(total_picks / pool_size) instead of
the unbounded overlap that independent per-video shuffles produce.
"""

import random
import threading


class SharedClipBag:
    """One shuffled deck per clip pool, shared by all videos of a batch.

    Batch stories render in parallel (STORY_BATCH_MAX_WORKERS), so draws are
    serialized under a lock. Each distinct pool — the selected libraries +
    clip-tag filter — gets its own deck, keyed via ``pool_key()``.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._decks: dict[tuple, list[tuple[str, float]]] = {}

    @staticmethod
    def pool_key(library_ids, clip_tags) -> tuple:
        """Identify a pool: stories with the same libraries and tag filter share a deck.

        ``library_ids`` is a single id or an iterable of ids (a render can draw
        from several libraries at once); they are sorted so the order the user
        ticked the libraries in never splits one pool into two decks.
        """
        if library_ids is None:
            raw_ids = []
        elif isinstance(library_ids, str):
            raw_ids = [library_ids]
        else:
            raw_ids = list(library_ids)
        ids = tuple(sorted({str(lid) for lid in raw_ids if str(lid or "").strip()}))
        tags = tuple(sorted(str(tag).lower() for tag in (clip_tags or [])))
        return (ids, tags)

    def draw(
        self,
        key: tuple,
        pool: list[tuple[str, float]],
        exclude: set[str] | frozenset = frozenset(),
    ) -> tuple[str, float]:
        """Draw one ``(clip_path, duration)`` from the shared deck.

        ``pool`` (re)fills the deck when it runs out. Clips in ``exclude`` —
        the ones the calling video already picked — are skipped while the deck
        still offers alternatives, so a refill happening mid-video cannot hand
        the same clip to that video twice. When every remaining clip is
        excluded (video needs more clips than the pool holds), the repeat is
        accepted rather than starving the draw.
        """
        if not pool:
            raise ValueError("SharedClipBag.draw() requires a non-empty pool")
        with self._lock:
            return self._pop_seed(self._deck(key, pool), exclude)

    def draw_run(
        self,
        key: tuple,
        pool: list[tuple[str, float]],
        want: int,
        successor,
        exclude: set[str] | frozenset = frozenset(),
    ) -> list[tuple[str, float]]:
        """Draw a seed clip plus up to ``want - 1`` of the clips that follow it.

        ``successor(path)`` names the next clip cut from the same source (or None).
        Consecutive clips of one source are contiguous pieces of it, so playing
        them back to back gives one seamless longer shot. A successor is taken
        only while it is still in the deck, so the no-replacement guarantee of
        ``draw()`` holds for every clip of the run.
        """
        if not pool:
            raise ValueError("SharedClipBag.draw_run() requires a non-empty pool")
        with self._lock:
            deck = self._deck(key, pool)
            run = [self._pop_seed(deck, exclude)]
            next_path = successor(run[-1][0])
            while len(run) < max(1, int(want)) and next_path and next_path not in exclude:
                idx = next((i for i, item in enumerate(deck) if item[0] == next_path), None)
                if idx is None:
                    break
                run.append(deck.pop(idx))
                next_path = successor(next_path)
            return run

    def _deck(self, key: tuple, pool: list[tuple[str, float]]) -> list[tuple[str, float]]:
        deck = self._decks.get(key)
        if not deck:
            deck = list(pool)
            random.shuffle(deck)
            self._decks[key] = deck
        return deck

    @staticmethod
    def _pop_seed(deck: list[tuple[str, float]], exclude) -> tuple[str, float]:
        if exclude:
            for i in range(len(deck) - 1, -1, -1):
                if deck[i][0] not in exclude:
                    return deck.pop(i)
        return deck.pop()
