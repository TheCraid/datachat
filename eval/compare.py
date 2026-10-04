"""Execution-match comparison between a predicted query result and the expected (gold) result.

A prediction counts as correct when it returns the same rows as the gold query for the gold
columns. Extra columns in the prediction are allowed (for example an id next to a name), column
order and names do not matter, row order does not matter, and numbers are compared after rounding
to one decimal place. Dates and timestamps are compared by their date part when the time is midnight.
"""

from __future__ import annotations

import itertools
from collections import Counter


def norm(v):
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        r = round(float(v), 1)
        return 0.0 if r == 0 else r
    s = str(v).strip()
    if len(s) >= 19 and s[10] == "T" and s[11:19] == "00:00:00":
        s = s[:10]
    return s.lower()


def _key(v):
    return (v is None, str(type(v).__name__), str(v))


def results_match(gold_rows: list[list], pred_rows: list[list], max_combos: int = 200) -> bool:
    if len(gold_rows) != len(pred_rows):
        return False
    if not gold_rows:
        return True
    g = [[norm(v) for v in r] for r in gold_rows]
    p = [[norm(v) for v in r] for r in pred_rows]
    g_cols = list(zip(*g, strict=True))
    p_cols = list(zip(*p, strict=True)) if p and p[0] else []
    candidates = []
    for gc in g_cols:
        gs = sorted(gc, key=_key)
        cand = [j for j, pc in enumerate(p_cols) if sorted(pc, key=_key) == gs]
        if not cand:
            return False
        candidates.append(cand)
    gold_counter = Counter(tuple(r) for r in g)
    for combo in itertools.islice(itertools.product(*candidates), max_combos):
        if len(set(combo)) != len(combo):
            continue
        if Counter(tuple(r[j] for j in combo) for r in p) == gold_counter:
            return True
    return False
