"""Chooses a chart from the shape of a query result.

Rule-based on purpose: predictable, instant and testable. The browser draws the chart from
this spec and the result rows.
"""

from __future__ import annotations

import re

_ORDINAL_NAME = re.compile(r"(year|month|quarter|week|day|hour|level|rating|rank|weekday|dow)", re.I)


def _is_ordinal(col: dict, rows: list[list], idx: int) -> bool:
    """Integer columns such as hour, month or level behave like categories, not measures."""
    if col["kind"] != "number":
        return False
    vals = [r[idx] for r in rows if r[idx] is not None]
    if not vals or not all(isinstance(v, int) or (isinstance(v, float) and v.is_integer()) for v in vals):
        return False
    return bool(_ORDINAL_NAME.search(col["name"])) and len(set(vals)) <= 60


def choose_chart(columns: list[dict], rows: list[list]) -> dict:
    if not rows:
        return {"type": "empty"}
    idx = {i: c for i, c in enumerate(columns)}
    nums = [i for i, c in idx.items() if c["kind"] == "number"]
    dates = [i for i, c in idx.items() if c["kind"] == "date"]
    texts = [i for i, c in idx.items() if c["kind"] in ("text", "boolean")]

    # A leading integer like hour/month/level is a category when another number follows it.
    if not texts and not dates and len(nums) >= 2 and _is_ordinal(columns[nums[0]], rows, nums[0]):
        texts = [nums[0]]
        nums = nums[1:]

    name = lambda i: columns[i]["name"]  # noqa: E731

    if len(rows) == 1 and nums and len(columns) <= 4:
        return {"type": "kpi", "values": [name(i) for i in nums[:3]],
                "label": name(texts[0]) if texts else None}
    if dates and nums:
        return {"type": "line", "x": name(dates[0]), "y": [name(i) for i in nums[:3]]}
    if texts and nums:
        values = [r[nums[0]] for r in rows if isinstance(r[nums[0]], (int, float))]
        negative = any(v < 0 for v in values)
        if len(rows) <= 30:
            kind = "diverging" if negative and any(v > 0 for v in values) else "bar"
            return {"type": kind, "x": name(texts[0]), "y": [name(nums[0])],
                    "ordered": texts[0] in [i for i, c in idx.items() if c["kind"] == "number"]}
        return {"type": "table"}
    if len(nums) >= 2 and len(rows) >= 3:
        return {"type": "scatter", "x": name(nums[0]), "y": [name(nums[1])]}
    return {"type": "table"}
