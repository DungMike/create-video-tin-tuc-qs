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
    serialized under a lock. Each distinct pool — library + clip-tag filter —
    gets its own deck, keyed via ``pool_key()``.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._decks: dict[tuple, list[tuple[str, float]]] = {}

    @staticmethod
    def pool_key(library_id, clip_tags) -> tuple:
        """Identify a pool: stories with the same library and tag filter share a deck."""
        tags = tuple(sorted(str(tag).lower() for tag in (clip_tags or [])))
        return (str(library_id or ""), tags)

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
            deck = self._decks.get(key)
            if not deck:
                deck = list(pool)
                random.shuffle(deck)
                self._decks[key] = deck
            if exclude:
                for i in range(len(deck) - 1, -1, -1):
                    if deck[i][0] not in exclude:
                        return deck.pop(i)
            return deck.pop()
