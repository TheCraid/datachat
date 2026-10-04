"""Input screening for questions, before any model call.

It catches the obvious cases cheaply. It is not the safety boundary: even if a request slips
through, the SQL checker and the locked, read-only database stop any change to data.
"""

from __future__ import annotations

import re

INJECTION = [
    r"\bignore\b.{0,40}\b(instructions?|rules?|prompts?|above|previous)\b",
    r"\b(system|developer|hidden)\s+(prompt|message|instructions?)\b",
    r"\byou\s+are\s+now\b",
    r"\b(jailbreak|dan\s+mode|developer\s+mode)\b",
    r"\b(reveal|print|show|repeat)\b.{0,30}\b(your|the)\s+(prompt|instructions|rules)\b",
    r"\bact\s+as\s+(an?\s+)?(unrestricted|different)\b",
]

WRITE_INTENT = [
    r"\b(delete|drop|truncate|wipe|erase|remove)\s+(all\s+|every\s+|the\s+)*(rows?|records?|tables?|data|database|"
    r"orders|customers|products|employees|everything)\b",
    r"\bupdate\s+\w+\s+set\b",
    r"\binsert\s+into\b",
    r"\balter\s+table\b",
    r"\b(change|modify|edit|overwrite)\s+(the\s+)?(data|values|records|rows)\b",
]


def check_question(text: str, max_chars: int = 500) -> tuple[bool, str]:
    q = (text or "").strip()
    if not q:
        return False, "Please type a question."
    if len(q) > max_chars:
        return False, f"Questions are limited to {max_chars} characters."
    low = q.lower()
    for pat in INJECTION:
        if re.search(pat, low):
            return False, "This looks like an attempt to change the assistant's instructions, so it was blocked."
    for pat in WRITE_INTENT:
        if re.search(pat, low):
            return False, "DataChat only reads data. It cannot change, add or delete anything."
    return True, ""
