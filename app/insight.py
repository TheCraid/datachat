"""Checks that every number in a model-written insight comes from the query result.

A model summarising a table can still invent or miscalculate a number. This check compares each
number in the text with the values in the result, allowing only for rounding.
"""

from __future__ import annotations

import re

_MULT = {"lakh": 1e5, "lakhs": 1e5, "l": 1e5, "crore": 1e7, "crores": 1e7, "cr": 1e7, "k": 1e3,
         "thousand": 1e3, "million": 1e6, "m": 1e6, "mn": 1e6, "billion": 1e9, "bn": 1e9}
_UNITS = r"lakhs?|crores?|cr|thousand|million|billion|bn|mn|k|l|m"
_NUM = re.compile(r"(?<![\w.])(-|−)?(\d[\d,]*(?:\.\d+)?)(\s*(?:" + _UNITS + r")\b)?(\s*%)?", re.IGNORECASE)


def extract_numbers(text: str) -> list[tuple[float, float, str]]:
    """Return (value, rounding tolerance, original text) for each number in the text."""
    out = []
    for m in _NUM.finditer(text):
        sign, digits, mult, _pct = m.groups()
        clean = digits.replace(",", "")
        try:
            value = float(clean)
        except ValueError:
            continue
        decimals = len(clean.split(".")[1]) if "." in clean else 0
        factor = _MULT.get((mult or "").strip().lower(), 1.0)
        unit = (10 ** -decimals) * factor
        value *= factor
        if sign:
            value = -value
        out.append((value, unit / 2 + 1e-9, m.group(0).strip()))
    return out


def _result_numbers(rows: list[list], columns: list[dict]) -> list[float]:
    nums: list[float] = []
    for row in rows:
        for v in row:
            if isinstance(v, bool):
                continue
            if isinstance(v, (int, float)):
                nums.append(float(v))
            elif isinstance(v, str):
                nums.extend(float(x) for x in re.findall(r"\d+(?:\.\d+)?", v)[:6])
    return nums


def unsupported_numbers(text: str, question: str, rows: list[list], columns: list[dict]) -> list[str]:
    values = _result_numbers(rows, columns)
    values += [float(len(rows)), float(len(columns))]
    values += [float(x) for x in re.findall(r"\d+(?:\.\d+)?", question.replace(",", ""))]
    bad = []
    for value, tol, raw in extract_numbers(text):
        a = abs(value)
        if a <= 12 and float(a).is_integer():
            continue  # small counts and ordinals ("three cities", "top 5", "Q3")
        if 1900 <= a <= 2100 and float(a).is_integer():
            continue  # years
        ok = any(abs(a - abs(v)) <= tol or abs(a - abs(v) * 100) <= tol for v in values)
        if not ok:
            bad.append(raw)
    return bad


def fallback_insight(columns: list[dict], rows: list[list], truncated: bool) -> str:
    """Plain, always-correct summary used when the model's text cannot be verified."""
    if not rows:
        return "No rows matched this question."
    n = f"{len(rows)}{'+' if truncated else ''}"
    first = ", ".join(f"{c['name']} = {_fmt(v)}" for c, v in zip(columns, rows[0], strict=False))
    return f"The query returned {n} row{'s' if len(rows) != 1 else ''}. First row: {first}."


def _fmt(v) -> str:
    if isinstance(v, float):
        return f"{v:,.2f}".rstrip("0").rstrip(".")
    return str(v)
