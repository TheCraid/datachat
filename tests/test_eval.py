import json
from pathlib import Path

import pytest

from app.sqlguard import check_sql
from eval.compare import results_match

QUESTIONS = [json.loads(x) for x in (Path(__file__).parents[1] / "eval" / "questions.jsonl").read_text().splitlines()
             if x.strip()]


def test_question_set_is_well_formed():
    ids = [q["id"] for q in QUESTIONS]
    assert len(ids) == len(set(ids)) >= 60
    assert {q["difficulty"] for q in QUESTIONS} == {"easy", "medium", "hard"}


@pytest.mark.parametrize("q", QUESTIONS, ids=lambda q: q["id"])
def test_every_gold_query_is_safe_and_returns_rows(registry, q):
    ds = registry.get(q["dataset"])
    rows = ds.execute(check_sql(q["sql"], ds.table_names()).sql).rows
    assert rows, f"{q['id']} returns no rows"


def test_match_ignores_row_order_column_order_and_extra_columns():
    gold = [["Pune", 10.0], ["Delhi", 20.0]]
    assert results_match(gold, [["Delhi", 20.0], ["Pune", 10.0]])
    assert results_match(gold, [[20.0, "Delhi"], [10.0, "Pune"]])
    assert results_match(gold, [[1, "Pune", 10.04], [2, "Delhi", 19.96]])


def test_match_rejects_wrong_values_and_pairings():
    gold = [["Pune", 10.0], ["Delhi", 20.0]]
    assert not results_match(gold, [["Pune", 20.0], ["Delhi", 10.0]])
    assert not results_match(gold, [["Pune", 10.0]])
    assert not results_match(gold, [["Pune", 10.0], ["Delhi", 25.0]])


def test_match_normalises_dates_and_case():
    assert results_match([["2026-01-01", 5]], [["2026-01-01T00:00:00", 5.0]])
    assert results_match([["North", 1]], [["north", 1]])
