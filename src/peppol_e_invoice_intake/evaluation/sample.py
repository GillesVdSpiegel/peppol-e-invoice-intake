"""Choose which documents a small evaluation run spends money on.

A ten-document run exists to shake out problems cheaply before the full corpus,
so the sample is chosen for coverage rather than drawn at random: every layout,
every language, and the cases that are hardest to read. A random ten would
likely miss the 42-line invoice and the reverse-charge one, which are exactly
the documents most likely to break.

Selection is deterministic, so a resumed or repeated run evaluates the same
documents and its numbers stay comparable.
"""

from __future__ import annotations

from collections import Counter

#: The documents most likely to break extraction, in the order they are taken.
#: Each is here for a specific reason, noted beside it.
PRIORITY: tuple[str, ...] = (
    "multi-page-line-items",        # 42 lines across a page break
    "reverse-charge-construction",  # AE: 0% rate whose category lives in the wording
    "intra-community-supply",       # K: needs a derived deliver-to country
    "rounding-boundaries",          # prices with a third decimal
    "four-vat-rates",               # four breakdown rows, one exempt
    "export-outside-eu",            # no derivable Peppol address; must not be invented
    "line-discounts",               # discount must be read separately from the price
    "fractional-quantities",        # 37.5 hours, decimal commas
)


def select_sample(entries: list[dict], size: int) -> list[dict]:
    """Pick `size` manifest entries, one per base invoice, maximising coverage.

    Each step takes the candidate that adds the most: an unused priority invoice
    first, then a layout or language not yet represented, then whichever is least
    represented so far. Ties go to manifest order.
    """
    if size <= 0:
        return []

    order = {id(entry): index for index, entry in enumerate(entries)}
    layouts = {entry["layout"] for entry in entries}
    languages = {entry["language"] for entry in entries}

    chosen: list[dict] = []
    used_keys: set[str] = set()
    layout_counts: Counter[str] = Counter()
    language_counts: Counter[str] = Counter()

    def value(entry: dict) -> tuple:
        priority = entry["key"] in PRIORITY
        return (
            priority,
            -PRIORITY.index(entry["key"]) if priority else 0,
            entry["layout"] not in layout_counts,
            entry["language"] not in language_counts,
            -layout_counts[entry["layout"]],
            -language_counts[entry["language"]],
            -order[id(entry)],
        )

    while len(chosen) < size:
        candidates = [entry for entry in entries if entry["key"] not in used_keys]
        if not candidates:
            break
        best = max(candidates, key=value)
        chosen.append(best)
        used_keys.add(best["key"])
        layout_counts[best["layout"]] += 1
        language_counts[best["language"]] += 1

    # Only report coverage gaps the sample could have closed.
    if len(chosen) >= len(layouts):
        missing = layouts - set(layout_counts)
        if missing:  # pragma: no cover - guarded by tests on the real catalogue
            raise ValueError(f"sample of {size} misses layouts {sorted(missing)}")
    if len(chosen) >= len(languages) and languages - set(language_counts):
        raise ValueError("sample misses a language")  # pragma: no cover

    return chosen
