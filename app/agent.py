"""The DataChat agent: a LangGraph state machine that turns a question into a checked, executed query.

    guard -> write_sql -> check_sql -> run_sql -> make_chart -> write_insight
                            ^   |          |
                            |   v          v
                            fix_sql  <-----+   (on a rejected or failed query, up to N times)

Every node appends human-readable events, which the API streams to the browser as they happen.
"""

from __future__ import annotations

import json
import operator
import time
from dataclasses import dataclass
from typing import Annotated, Any, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from app import prompts
from app.charts import choose_chart
from app.config import Settings
from app.datasets import Dataset, QueryError
from app.guardrails import check_question
from app.insight import fallback_insight, unsupported_numbers
from app.llm import LLMClient, LLMError, Usage
from app.sqlguard import SQLRejected, check_sql


@dataclass
class Deps:
    llm: LLMClient
    dataset: Dataset
    settings: Settings
    usage: Usage


class AgentState(TypedDict, total=False):
    question: str
    history: list[dict]          # [{"question": ..., "sql": ...}] from earlier turns
    sql: str                     # the model's latest SQL
    display_sql: str
    runnable_sql: str
    error: str
    attempts: int                # fixes used so far
    result: dict
    chart: dict
    insight: str
    insight_verified: bool
    insight_reason: str          # why a plain summary was used: unverified | unavailable
    status: str                  # answered | blocked | not_answerable | failed | error
    message: str
    events: Annotated[list[dict], operator.add]
    timings_ms: Annotated[dict, lambda a, b: {**(a or {}), **(b or {})}]


def _deps(config: RunnableConfig) -> Deps:
    return config["configurable"]["deps"]


def _event(step: str, status: str, detail: str, **extra: Any) -> dict:
    return {"step": step, "status": status, "detail": detail, **extra}


def _timed(name: str):
    def wrap(fn):
        def inner(state: AgentState, config: RunnableConfig) -> dict:
            t = time.perf_counter()
            out = fn(state, config)
            before = (state.get("timings_ms") or {}).get(name, 0)
            out["timings_ms"] = {name: round((time.perf_counter() - t) * 1000 + before, 1)}
            return out
        inner.__name__ = fn.__name__
        return inner
    return wrap


# --------------------------------------------------------------------------- nodes

@_timed("guard")
def guard(state: AgentState, config: RunnableConfig) -> dict:
    d = _deps(config)
    ok, reason = check_question(state["question"], d.settings.max_question_chars)
    if not ok:
        return {"status": "blocked", "message": reason, "events": [_event("guardrail", "blocked", reason)]}
    return {"attempts": 0, "events": [_event("guardrail", "pass", "A read-only question about the data")]}


def _sql_messages(state: AgentState, d: Deps) -> list[dict]:
    system = prompts.SQL_SYSTEM.replace("{schema}", d.dataset.schema_text())
    msgs = [{"role": "system", "content": system}, *prompts.SQL_FEW_SHOT]
    history = state.get("history") or []
    if history:
        lines = [prompts.HISTORY_INTRO]
        for h in history[-d.settings.history_turns:]:
            lines.append(f"Q: {h['question']}\nSQL: {h['sql']}")
        msgs.append({"role": "user", "content": "\n\n".join(lines)})
        msgs.append({"role": "assistant", "content": '{"answerable": true, "sql": "", "message": "Noted."}'})
    msgs.append({"role": "user", "content": f"Question: {state['question']}"})
    return msgs


@_timed("write_sql")
def write_sql(state: AgentState, config: RunnableConfig) -> dict:
    d = _deps(config)
    try:
        out = d.llm.json("sql", _sql_messages(state, d), d.usage)
    except LLMError as exc:
        return {"status": "error", "message": str(exc), "events": [_event("write_sql", "error", str(exc))]}
    if out.get("answerable") is False or not str(out.get("sql") or "").strip():
        msg = str(out.get("message") or "This question cannot be answered from this dataset.")
        return {"status": "not_answerable", "message": msg, "events": [_event("write_sql", "info", msg)]}
    return {"sql": str(out["sql"]), "message": str(out.get("message") or ""),
            "events": [_event("write_sql", "pass", str(out.get("message") or "Wrote a query"))]}


@_timed("check_sql")
def check(state: AgentState, config: RunnableConfig) -> dict:
    d = _deps(config)
    try:
        c = check_sql(state["sql"], d.dataset.table_names(), d.settings.max_result_rows)
    except SQLRejected as exc:
        return {"error": f"Rejected by the safety check: {exc}",
                "events": [_event("check_sql", "error", f"Rejected: {exc}")]}
    return {"error": "", "runnable_sql": c.sql, "display_sql": c.display_sql,
            "events": [_event("check_sql", "pass", "Single read-only SELECT on known tables; row limit applied",
                              sql=c.display_sql)]}


@_timed("run_sql")
def run(state: AgentState, config: RunnableConfig) -> dict:
    d = _deps(config)
    try:
        r = d.dataset.execute(state["runnable_sql"], d.settings.max_result_rows, d.settings.query_timeout_s)
    except QueryError as exc:
        return {"error": f"The database returned an error: {exc}",
                "events": [_event("run_sql", "error", f"Run failed: {exc}")]}
    result = {"columns": r.columns, "rows": r.rows, "truncated": r.truncated, "elapsed_ms": r.elapsed_ms}
    rows = f"{len(r.rows)}{'+' if r.truncated else ''} row{'s' if len(r.rows) != 1 else ''}"
    return {"error": "", "result": result, "events": [_event("run_sql", "pass", f"{rows} in {r.elapsed_ms:.0f} ms")]}


@_timed("fix_sql")
def fix(state: AgentState, config: RunnableConfig) -> dict:
    d = _deps(config)
    msgs = _sql_messages(state, d)
    msgs.append({"role": "assistant", "content": json.dumps({"answerable": True, "sql": state["sql"]})})
    msgs.append({"role": "user", "content": prompts.FIX_USER.format(sql=state["sql"], error=state["error"],
                                                                   question=state["question"])})
    attempts = state.get("attempts", 0) + 1
    try:
        out = d.llm.json("sql", msgs, d.usage)
    except LLMError as exc:
        return {"attempts": attempts, "status": "error", "message": str(exc),
                "events": [_event("fix_sql", "error", str(exc))]}
    new_sql = str(out.get("sql") or "").strip()
    if not new_sql:
        return {"attempts": attempts, "status": "failed", "message": "The model could not fix the query.",
                "events": [_event("fix_sql", "error", "No corrected query returned")]}
    return {"attempts": attempts, "sql": new_sql,
            "events": [_event("fix_sql", "fixed", str(out.get("message") or "Rewrote the query after the error"))]}


@_timed("make_chart")
def make_chart(state: AgentState, config: RunnableConfig) -> dict:
    r = state["result"]
    spec = choose_chart(r["columns"], r["rows"])
    label = {"kpi": "big number", "line": "line chart over time", "bar": "bar chart",
             "diverging": "diverging bar chart", "scatter": "scatter plot",
             "table": "table (no clear chart for this shape)", "empty": "nothing to plot"}
    return {"chart": spec, "events": [_event("make_chart", "pass", f"Chart: {label.get(spec['type'], spec['type'])}")]}


def _table_text(result: dict, limit: int = 40) -> str:
    cols = [c["name"] for c in result["columns"]]
    lines = [" | ".join(cols)]
    for row in result["rows"][:limit]:
        lines.append(" | ".join("NULL" if v is None else str(v) for v in row))
    if len(result["rows"]) > limit:
        lines.append(f"... ({len(result['rows']) - limit} more rows not shown)")
    return "\n".join(lines)


@_timed("write_insight")
def write_insight(state: AgentState, config: RunnableConfig) -> dict:
    d = _deps(config)
    r = state["result"]
    if not r["rows"]:
        text = "No rows matched this question. Try widening the filters or the date range."
        return {"insight": text, "insight_verified": True, "status": "answered",
                "events": [_event("write_insight", "pass", "Empty result explained")]}
    msgs = [
        {"role": "system", "content": prompts.INSIGHT_SYSTEM},
        {"role": "user", "content": prompts.INSIGHT_USER.format(
            question=state["question"], n_rows=len(r["rows"]), truncated=", more not shown" if r["truncated"] else "",
            table=_table_text(r))},
    ]
    text, bad, unavailable = "", [], False
    for _ in range(2):
        try:
            out = d.llm.json("small", msgs, d.usage)
        except LLMError:
            unavailable = True
            break
        text = str(out.get("insight") or "").strip()
        bad = unsupported_numbers(text, state["question"], r["rows"], r["columns"]) if text else ["(empty)"]
        if not bad:
            return {"insight": text, "insight_verified": True, "status": "answered",
                    "events": [_event("write_insight", "pass", "Every number checked against the result")]}
        msgs = msgs + [{"role": "assistant", "content": json.dumps({"insight": text})},
                       {"role": "user", "content": prompts.INSIGHT_RETRY.format(bad=", ".join(bad))}]
    fb = fallback_insight(r["columns"], r["rows"], r["truncated"])
    if unavailable and not bad:
        reason, detail = "unavailable", "The insight model did not respond; used a plain summary instead"
    else:
        reason = "unverified"
        detail = f"Model text had unverifiable numbers ({', '.join(bad) or 'none returned'}); used a plain summary"
    return {"insight": fb, "insight_verified": False, "insight_reason": reason, "status": "answered",
            "events": [_event("write_insight", "error", detail)]}


# --------------------------------------------------------------------------- edges

def after_guard(state: AgentState) -> str:
    return END if state.get("status") == "blocked" else "write_sql"


def after_write(state: AgentState) -> str:
    return END if state.get("status") in ("not_answerable", "error") else "check_sql"


def _retry_or_fail(state: AgentState, config: RunnableConfig) -> str:
    return "fix_sql" if state.get("attempts", 0) < _deps(config).settings.max_fix_attempts else "give_up"


def after_check(state: AgentState, config: RunnableConfig) -> str:
    return "run_sql" if not state.get("error") else _retry_or_fail(state, config)


def after_run(state: AgentState, config: RunnableConfig) -> str:
    return "make_chart" if not state.get("error") else _retry_or_fail(state, config)


def after_fix(state: AgentState) -> str:
    return END if state.get("status") in ("error", "failed") else "check_sql"


def give_up(state: AgentState, config: RunnableConfig) -> dict:
    msg = "Could not write a working query for this question. Try rephrasing it or naming the columns to use."
    return {"status": "failed", "message": msg, "events": [_event("give_up", "error", msg)]}


def build_graph(with_insight: bool = True):
    """The full graph for the app; the evaluation uses with_insight=False to measure SQL alone."""
    g = StateGraph(AgentState)
    g.add_node("guard", guard)
    g.add_node("write_sql", write_sql)
    g.add_node("check_sql", check)
    g.add_node("run_sql", run)
    g.add_node("fix_sql", fix)
    g.add_node("give_up", give_up)
    g.add_node("make_chart", make_chart)
    if with_insight:
        g.add_node("write_insight", write_insight)
    g.add_edge(START, "guard")
    g.add_conditional_edges("guard", after_guard, ["write_sql", END])
    g.add_conditional_edges("write_sql", after_write, ["check_sql", END])
    g.add_conditional_edges("check_sql", after_check, ["run_sql", "fix_sql", "give_up"])
    g.add_conditional_edges("run_sql", after_run, ["make_chart", "fix_sql", "give_up"])
    g.add_conditional_edges("fix_sql", after_fix, ["check_sql", END])
    g.add_edge("give_up", END)
    if with_insight:
        g.add_edge("make_chart", "write_insight")
        g.add_edge("write_insight", END)
    else:
        g.add_node("finish", lambda state: {"status": "answered"})
        g.add_edge("make_chart", "finish")
        g.add_edge("finish", END)
    return g.compile()


GRAPH = build_graph()
GRAPH_SQL_ONLY = build_graph(with_insight=False)


def stream_answer(question: str, history: list[dict], deps: Deps):
    """Yield (node_name, update) pairs as each step of the graph finishes."""
    config = {"configurable": {"deps": deps}, "recursion_limit": 30}
    yield from ((node, upd) for chunk in GRAPH.stream({"question": question, "history": history, "events": []},
                                                      config=config, stream_mode="updates")
                for node, upd in chunk.items())


def answer(question: str, history: list[dict], deps: Deps, with_insight: bool = True) -> AgentState:
    """Run the graph to completion (used by the evaluation)."""
    config = {"configurable": {"deps": deps}, "recursion_limit": 30}
    graph = GRAPH if with_insight else GRAPH_SQL_ONLY
    return graph.invoke({"question": question, "history": history, "events": []}, config=config)
