"""Measure DataChat's execution accuracy on the gold question set.

Examples:
    python -m eval.run_eval                                  # default model from settings
    python -m eval.run_eval --model openai/gpt-oss-20b --label "gpt-oss-20b"
    python -m eval.run_eval --only s01,s16 --verbose
    python -m eval.run_eval --check-gold                     # only check that every gold query runs
    # Any OpenAI-compatible server works, for example a local Ollama model:
    python -m eval.run_eval --base-url http://localhost:11434/v1 --api-key ollama --model sqlmate --kind finetuned

Each run is saved under its label in eval/results/latest.json (read by the Benchmark page), with
per-question details in eval/results/<label>.details.json and a summary in eval/results/report.md.
The fallback model is switched off during evaluation, so each run measures exactly one model.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from app.agent import Deps, answer
from app.config import Settings
from app.datasets import DatasetRegistry, QueryError
from app.llm import LLMClient, OpenAICompatibleBackend, Usage
from app.sqlguard import check_sql
from eval.compare import results_match

HERE = Path(__file__).resolve().parent
QUESTIONS = HERE / "questions.jsonl"
RESULTS = HERE / "results"


def load_questions(only: str | None = None, dataset: str | None = None) -> list[dict]:
    qs = [json.loads(line) for line in QUESTIONS.read_text(encoding="utf-8").splitlines() if line.strip()]
    if only:
        keep = {x.strip() for x in only.split(",")}
        qs = [q for q in qs if q["id"] in keep]
    if dataset:
        qs = [q for q in qs if q["dataset"] == dataset]
    return qs


def run_gold(registry: DatasetRegistry, q: dict, max_rows: int = 1000) -> list[list]:
    ds = registry.get(q["dataset"])
    c = check_sql(q["sql"], ds.table_names(), max_rows)
    return ds.execute(c.sql, max_rows).rows


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "run"


def summarise(details: list[dict]) -> dict:
    n = len(details)
    acc = lambda rows: round(sum(r["correct"] for r in rows) / len(rows), 4) if rows else None  # noqa: E731
    by_diff = {d: acc([r for r in details if r["difficulty"] == d]) for d in ("easy", "medium", "hard")}
    by_ds = {d: acc([r for r in details if r["dataset"] == d]) for d in sorted({r["dataset"] for r in details})}
    lat = [r["latency_ms"] for r in details]
    return {
        "accuracy": acc(details),
        "by_difficulty": by_diff,
        "by_dataset": by_ds,
        "self_fixed_rate": round(sum(r["attempts"] > 0 for r in details) / n, 4) if n else None,
        "no_result_rate": round(sum(r["status"] != "answered" for r in details) / n, 4) if n else None,
        "latency_ms_p50": round(statistics.median(lat), 1) if lat else None,
        "cost_per_1k_usd": round(1000 * sum(r["cost_usd"] for r in details) / n, 4) if n else None,
        "n": n,
    }


def write_report(data: dict) -> None:
    lines = ["# DataChat evaluation report", "",
             f"Questions: {data['n_questions']} · last updated {data['generated_at'][:16].replace('T', ' ')} UTC", "",
             "| Model | Accuracy | Easy | Medium | Hard | Needed a fix | No result | Median latency | Cost / 1k |",
             "|---|---|---|---|---|---|---|---|---|"]
    pc = lambda v: "–" if v is None else f"{v * 100:.1f}%"  # noqa: E731
    for r in data["runs"]:
        d = r["by_difficulty"]
        lines.append(f"| {r['label']} | **{pc(r['accuracy'])}** | {pc(d.get('easy'))} | {pc(d.get('medium'))} | "
                     f"{pc(d.get('hard'))} | {pc(r['self_fixed_rate'])} | {pc(r['no_result_rate'])} | "
                     f"{(r['latency_ms_p50'] or 0) / 1000:.1f} s | ${r['cost_per_1k_usd'] or 0:.2f} |")
    lines += ["", "Execution accuracy: a question is correct when the query returns the same rows as the gold query "
              "(extra columns allowed, numbers compared after rounding to 1 decimal). See `eval/compare.py`.", ""]
    (RESULTS / "report.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", help="SQL model to evaluate (default: SQL_MODEL from settings)")
    ap.add_argument("--label", help="name shown in the report (default: the model name)")
    ap.add_argument("--kind", default="api", choices=["api", "base", "finetuned"], help="for the Benchmark page")
    ap.add_argument("--note", default="", help="short description shown under the label")
    ap.add_argument("--base-url", help="OpenAI-compatible base URL (default: LLM_BASE_URL)")
    ap.add_argument("--api-key", help="API key for --base-url (default: GROQ_API_KEY)")
    ap.add_argument("--only", help="comma-separated question ids")
    ap.add_argument("--dataset", choices=["store", "food", "hr"])
    ap.add_argument("--sleep", type=float, default=0.0, help="seconds between questions (free-tier rate limits)")
    ap.add_argument("--min-accuracy", type=float, default=None, help="exit with an error below this (0-1)")
    ap.add_argument("--check-gold", action="store_true", help="only check that the gold queries run")
    ap.add_argument("--no-save", action="store_true", help="print results without updating eval/results")
    ap.add_argument("--allow-errors", action="store_true",
                    help="save results even if some questions failed because the API was unavailable")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    questions = load_questions(args.only, args.dataset)
    registry = DatasetRegistry()
    registry.load_samples(sorted({q["dataset"] for q in questions}))

    if args.check_gold:
        bad = 0
        for q in questions:
            try:
                rows = run_gold(registry, q)
                status = f"{len(rows)} rows" if rows else "EMPTY"
                bad += not rows
            except QueryError as exc:
                status, bad = f"ERROR {exc}", bad + 1
            print(f"{q['id']:>4}  {status}")
        print(f"\n{len(questions) - bad}/{len(questions)} gold queries return rows.")
        return 1 if bad else 0

    # One model per run (no fallback), and patient waiting on free-tier rate limits.
    overrides = {"fallback_model": "", "rate_limit_retries": 10, "rate_limit_max_wait_s": 90.0}
    if args.model:
        overrides["sql_model"] = args.model
    if args.base_url:
        overrides["llm_base_url"] = args.base_url
    if args.api_key:
        overrides["groq_api_key"] = args.api_key
    settings = Settings(**overrides)
    llm = LLMClient(OpenAICompatibleBackend(settings), settings)
    label = args.label or settings.sql_model

    details = []
    for i, q in enumerate(questions, 1):
        usage = Usage()
        start = time.perf_counter()
        state = answer(q["question"], [], Deps(llm, registry.get(q["dataset"]), settings, usage), with_insight=False)
        latency = (time.perf_counter() - start) * 1000
        gold = run_gold(registry, q)
        pred = (state.get("result") or {}).get("rows")
        correct = pred is not None and results_match(gold, pred)
        details.append({"id": q["id"], "dataset": q["dataset"], "difficulty": q["difficulty"],
                        "question": q["question"], "correct": correct, "status": state.get("status", "error"),
                        "attempts": state.get("attempts", 0),
                        "sql": state.get("display_sql") or state.get("sql"), "message": state.get("message", ""),
                        "latency_ms": round(latency, 1), "cost_usd": usage.cost_usd,
                        "tokens": usage.input_tokens + usage.output_tokens})
        mark = "PASS" if correct else "FAIL"
        print(f"[{i:>2}/{len(questions)}] {mark} {q['id']} ({q['difficulty']}) {latency / 1000:.1f}s"
              + (f"  fixes={state.get('attempts', 0)}" if state.get("attempts") else ""))
        if args.verbose and not correct:
            print("      Q:", q["question"])
            print("      SQL:", (state.get("display_sql") or state.get("sql") or "-").replace("\n", " ")[:400])
            print("      status:", state.get("status"), state.get("message", ""))
        if args.sleep:
            time.sleep(args.sleep)

    api_errors = [r for r in details if r["status"] == "error"]
    for r in details:
        r["api_error"] = r["status"] == "error"
    summary = summarise(details)
    print(f"\n{label}: execution accuracy {summary['accuracy'] * 100:.1f}% on {summary['n']} questions "
          f"(easy {summary['by_difficulty']['easy']}, medium {summary['by_difficulty']['medium']}, "
          f"hard {summary['by_difficulty']['hard']})")

    if api_errors:
        print(f"\nWARNING: {len(api_errors)} question(s) failed because the model API did not answer "
              f"({', '.join(r['id'] for r in api_errors)}). Accuracy above counts them as wrong.")
        if not args.allow_errors:
            print("Results were NOT saved. Wait a few minutes and run again (use --sleep 10 on the free tier).")
            args.no_save = True

    if not args.no_save and not args.only and not args.dataset:
        RESULTS.mkdir(parents=True, exist_ok=True)
        latest = RESULTS / "latest.json"
        data = json.loads(latest.read_text(encoding="utf-8")) if latest.exists() else {"runs": []}
        run = {"label": label, "model": settings.sql_model, "kind": args.kind, "note": args.note, **summary}
        data["runs"] = [r for r in data.get("runs", []) if r["label"] != label] + [run]
        order = {"api": 0, "base": 1, "finetuned": 2}
        data["runs"].sort(key=lambda r: (order.get(r.get("kind"), 3), -(r.get("accuracy") or 0)))
        data["generated_at"] = datetime.now(UTC).isoformat()
        data["n_questions"] = len(questions)
        latest.write_text(json.dumps(data, indent=2), encoding="utf-8")
        (RESULTS / f"{slug(label)}.details.json").write_text(json.dumps(details, indent=2), encoding="utf-8")
        write_report(data)
        print(f"Saved to {latest.relative_to(HERE.parent)} and eval/results/report.md")

    if args.min_accuracy is not None and summary["accuracy"] < args.min_accuracy:
        print(f"Accuracy is below the gate of {args.min_accuracy * 100:.0f}%.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
