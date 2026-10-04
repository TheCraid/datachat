"""FastAPI app: datasets, uploads, the streaming /api/ask endpoint, pins, benchmark and the web UI."""

from __future__ import annotations

import json
import logging
import re
import statistics
import threading
import time
from collections import OrderedDict, defaultdict, deque
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app import prompts
from app.agent import Deps, stream_answer
from app.charts import choose_chart
from app.config import Settings, get_settings
from app.datasets import DatasetError, DatasetRegistry, QueryError, load_upload
from app.llm import ChatBackend, LLMClient, LLMError, OpenAICompatibleBackend, Usage
from app.sqlguard import SQLRejected, check_sql
from app.store import Store

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("datachat")

ROOT = Path(__file__).resolve().parents[1]
STATIC = Path(__file__).resolve().parent / "static"
BENCHMARK_FILE = ROOT / "eval" / "results" / "latest.json"
ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


class AskRequest(BaseModel):
    dataset_id: str = Field(..., max_length=64)
    question: str = Field(..., min_length=1, max_length=2000)
    session_id: str = Field(..., max_length=64)
    conversation_id: str = Field(..., max_length=64)


class ExplainRequest(BaseModel):
    sql: str = Field(..., min_length=1, max_length=8000)


class PinRequest(BaseModel):
    session_id: str = Field(..., max_length=64)
    dataset_id: str = Field(..., max_length=64)
    question: str = Field(..., max_length=2000)
    sql: str = Field(..., max_length=8000)


class RateLimiter:
    def __init__(self, per_minute: int):
        self.per_minute, self.hits, self.lock = per_minute, defaultdict(deque), threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self.lock:
            q = self.hits[key]
            while q and now - q[0] > 60:
                q.popleft()
            if len(q) >= self.per_minute:
                return False
            q.append(now)
            return True


class Stats:
    def __init__(self, window: int = 500):
        self.rows: deque[dict] = deque(maxlen=window)
        self.lock = threading.Lock()

    def record(self, row: dict) -> None:
        with self.lock:
            self.rows.append(row)

    def summary(self) -> dict:
        with self.lock:
            rows = list(self.rows)
        if not rows:
            return {"questions": 0}
        lat = sorted(r["latency_ms"] for r in rows)
        answered = [r for r in rows if r["status"] == "answered"]
        return {
            "questions": len(rows),
            "latency_ms_p50": round(statistics.median(lat), 1),
            "latency_ms_p95": round(lat[min(len(lat) - 1, int(0.95 * len(lat)))], 1),
            "status_counts": {s: sum(r["status"] == s for r in rows)
                              for s in ("answered", "blocked", "not_answerable", "failed", "error")},
            "self_fixed_rate": round(sum(r["attempts"] > 0 for r in answered) / len(answered), 3) if answered else None,
            "cache_hits": sum(r.get("cached", False) for r in rows),
            "avg_cost_usd": round(sum(r["cost_usd"] for r in rows) / len(rows), 6),
        }


class AnswerCache:
    """Repeated questions on the same data return instantly without new model calls."""

    def __init__(self, size: int = 256):
        self.items: OrderedDict[tuple, list[tuple[str, dict]]] = OrderedDict()
        self.size, self.lock = size, threading.Lock()

    def get(self, key: tuple):
        with self.lock:
            if key in self.items:
                self.items.move_to_end(key)
                return self.items[key]
        return None

    def put(self, key: tuple, events: list[tuple[str, dict]]) -> None:
        with self.lock:
            self.items[key] = events
            while len(self.items) > self.size:
                self.items.popitem(last=False)


def sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


def create_app(settings: Settings | None = None, backend: ChatBackend | None = None,
               registry: DatasetRegistry | None = None, store: Store | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if app.state.registry is None:
            reg = DatasetRegistry(settings.max_sessions, settings.session_ttl_min)
            t = time.perf_counter()
            reg.load_samples()
            log.info("Loaded sample datasets in %.1f s", time.perf_counter() - t)
            app.state.registry = reg
        if app.state.store is None:
            app.state.store = Store(settings.database_url)
        yield

    app = FastAPI(title="DataChat", version="1.0.0", lifespan=lifespan,
                  description="Ask questions about your data in plain English; get SQL, a chart and an insight.")
    app.state.registry = registry
    app.state.store = store
    app.state.stats = Stats()
    app.state.cache = AnswerCache()
    limiter = RateLimiter(settings.rate_limit_per_min)

    llm: LLMClient | None = None
    llm_error = ""
    try:
        llm = LLMClient(backend or OpenAICompatibleBackend(settings), settings)
    except LLMError as exc:
        llm_error = str(exc)
        log.warning("%s", exc)

    def check_rate(request: Request) -> None:
        key = request.headers.get("x-forwarded-for", "").split(",")[0].strip() or (
            request.client.host if request.client else "unknown")
        if not limiter.allow(key):
            raise HTTPException(429, "Too many requests. Please wait a minute.")

    def get_dataset(dataset_id: str):
        try:
            return app.state.registry.get(dataset_id)
        except KeyError:
            raise HTTPException(404, "This dataset has expired or does not exist. Upload it again.") from None

    def check_id(value: str, what: str) -> None:
        if not ID_RE.match(value):
            raise HTTPException(422, f"Invalid {what}.")

    # ------------------------------------------------------------------ data
    @app.get("/api/health")
    def health():
        return {"status": "ok", "llm_configured": llm is not None,
                "samples": sorted(app.state.registry.samples) if app.state.registry else []}

    @app.get("/api/datasets")
    def datasets():
        out = []
        for ds in app.state.registry.samples.values():
            out.append({"id": ds.id, "title": ds.title, "tables": [{"name": t.name, "rows": t.row_count}
                                                                   for t in ds.tables]})
        return out

    @app.get("/api/datasets/{dataset_id}")
    def dataset(dataset_id: str):
        return get_dataset(dataset_id).summary()

    @app.post("/api/upload")
    async def upload(request: Request, file: UploadFile = File(...)):
        check_rate(request)
        limit = settings.max_upload_mb * 1024 * 1024
        data = await file.read(limit + 1)
        if len(data) > limit:
            raise HTTPException(413, f"Files are limited to {settings.max_upload_mb} MB.")
        try:
            ds = load_upload(file.filename or "upload.csv", data, settings.max_upload_rows)
        except DatasetError as exc:
            raise HTTPException(400, str(exc)) from exc
        app.state.registry.add_upload(ds)
        return ds.summary()

    # ------------------------------------------------------------------ asking
    @app.post("/api/ask")
    def ask(body: AskRequest, request: Request):
        check_rate(request)
        check_id(body.session_id, "session id")
        check_id(body.conversation_id, "conversation id")
        ds = get_dataset(body.dataset_id)
        question = body.question.strip()
        store: Store = app.state.store
        history = store.conversation(body.conversation_id, ds.id, settings.history_turns)
        key = (ds.id, " ".join(question.lower().split()), tuple(h["sql"] for h in history))

        def generate():
            start = time.perf_counter()
            if llm is None:
                yield sse("error", {"message": llm_error})
                return
            cached = app.state.cache.get(key)
            final: dict = {"status": "error", "message": "", "sql": None, "attempts": 0}
            usage = Usage()
            emitted: list[tuple[str, dict]] = []
            try:
                if cached:
                    yield sse("step", {"step": "cache", "status": "pass", "detail": "Same question on the same data: "
                                                                                     "reused the earlier answer"})
                    for ev, data in cached:
                        if ev == "done_state":
                            final.update(data)
                        else:
                            yield sse(ev, data)
                else:
                    deps = Deps(llm=llm, dataset=ds, settings=settings, usage=usage)
                    for _node, upd in stream_answer(question, history, deps):
                        out: list[tuple[str, dict]] = [("step", e) for e in upd.get("events", [])]
                        if upd.get("display_sql"):
                            final["sql"] = upd["display_sql"]
                            out.append(("sql", {"sql": upd["display_sql"]}))
                        if "result" in upd:
                            out.append(("result", upd["result"]))
                        if "chart" in upd:
                            out.append(("chart", upd["chart"]))
                        if "insight" in upd:
                            out.append(("insight", {"text": upd["insight"],
                                                    "verified": upd.get("insight_verified", False),
                                                    "reason": upd.get("insight_reason", "")}))
                        for k in ("status", "message", "attempts"):
                            if k in upd:
                                final[k] = upd[k]
                        for ev, data in out:
                            emitted.append((ev, data))
                            yield sse(ev, data)
            except Exception as exc:  # never leave the browser hanging
                log.exception("ask failed")
                final.update(status="error", message=f"Something went wrong: {type(exc).__name__}")
            latency = round((time.perf_counter() - start) * 1000, 1)
            if final["status"] == "answered" and not cached:
                app.state.cache.put(key, emitted + [("done_state", {k: final[k] for k in ("status", "message",
                                                                                          "sql", "attempts")})])
            metrics = {"latency_ms": latency, **usage.summary(), "attempts": final.get("attempts", 0),
                       "cached": bool(cached)}
            yield sse("done", {"status": final["status"], "message": final["message"], "sql": final["sql"],
                               "metrics": metrics})
            try:
                store.add_history(session_id=body.session_id, conversation_id=body.conversation_id,
                                  dataset_id=ds.id, question=question, sql=final["sql"], status=final["status"],
                                  latency_ms=latency, cost_usd=usage.cost_usd)
            except Exception:
                log.exception("could not save history")
            app.state.stats.record({"latency_ms": latency, "status": final["status"], "cost_usd": usage.cost_usd,
                                    "attempts": final.get("attempts", 0), "cached": bool(cached)})

        return StreamingResponse(generate(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.post("/api/explain")
    def explain(body: ExplainRequest, request: Request):
        check_rate(request)
        if llm is None:
            raise HTTPException(503, llm_error)
        try:
            out = llm.json("small", [{"role": "system", "content": prompts.EXPLAIN_SYSTEM},
                                     {"role": "user", "content": body.sql}], Usage())
        except LLMError as exc:
            raise HTTPException(502, str(exc)) from exc
        return {"explanation": str(out.get("explanation") or "")}

    # ------------------------------------------------------------------ dashboard pins
    def run_saved(dataset_id: str, sql: str) -> dict:
        try:
            ds = app.state.registry.get(dataset_id)
        except KeyError:
            return {"state": "expired"}
        try:
            c = check_sql(sql, ds.table_names(), settings.max_result_rows)
            r = ds.execute(c.sql, settings.max_result_rows, settings.query_timeout_s)
        except (SQLRejected, QueryError) as exc:
            return {"state": "error", "message": str(exc)}
        result = {"columns": r.columns, "rows": r.rows, "truncated": r.truncated}
        return {"state": "ok", "result": result, "chart": choose_chart(r.columns, r.rows)}

    @app.get("/api/pins")
    def list_pins(session_id: str):
        check_id(session_id, "session id")
        out = []
        for p in app.state.store.list_pins(session_id):
            out.append({"id": p["id"], "dataset_id": p["dataset_id"], "dataset_title": p["dataset_title"],
                        "question": p["question"], "sql": p["sql"], **run_saved(p["dataset_id"], p["sql"])})
        return out

    @app.post("/api/pins")
    def add_pin(body: PinRequest):
        check_id(body.session_id, "session id")
        ds = get_dataset(body.dataset_id)
        try:
            check_sql(body.sql, ds.table_names(), settings.max_result_rows)
        except SQLRejected as exc:
            raise HTTPException(400, str(exc)) from exc
        pin_id = app.state.store.add_pin(session_id=body.session_id, dataset_id=ds.id, dataset_title=ds.title,
                                         question=body.question, sql=body.sql)
        return {"id": pin_id}

    @app.delete("/api/pins/{pin_id}")
    def delete_pin(pin_id: int, session_id: str):
        check_id(session_id, "session id")
        if not app.state.store.delete_pin(session_id, pin_id):
            raise HTTPException(404, "Pin not found.")
        return {"deleted": pin_id}

    # ------------------------------------------------------------------ benchmark and metrics
    @app.get("/api/benchmark")
    def benchmark():
        if not BENCHMARK_FILE.exists():
            return {"available": False}
        return {"available": True, **json.loads(BENCHMARK_FILE.read_text(encoding="utf-8"))}

    @app.get("/api/stats")
    def stats():
        return app.state.stats.summary()

    # ------------------------------------------------------------------ web UI
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC / "index.html")

    return app


app = create_app()
