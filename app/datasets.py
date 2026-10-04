"""Datasets: loading sample data and uploads into DuckDB, profiling, and safe query execution.

Each dataset lives in its own in-memory DuckDB database. After loading, file and network access
is switched off and the configuration is locked, so a query can only read the dataset's own tables.
"""

from __future__ import annotations

import csv
import io
import re
import tempfile
import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from datetime import time as dtime
from decimal import Decimal
from pathlib import Path

import duckdb

from app.samples import BUILDERS, SampleDataset

NUMERIC_TYPES = ("TINYINT", "SMALLINT", "INTEGER", "BIGINT", "HUGEINT", "UTINYINT", "USMALLINT", "UINTEGER",
                 "UBIGINT", "FLOAT", "DOUBLE", "REAL", "DECIMAL")
TEMPORAL_TYPES = ("DATE", "TIMESTAMP", "TIME")


class DatasetError(ValueError):
    """A problem with the user's data or file (shown to the user)."""


class QueryError(RuntimeError):
    """The database rejected or failed to run a query (fed back to the agent)."""


@dataclass
class Column:
    name: str
    type: str
    description: str = ""
    null_pct: float = 0.0
    distinct: int = 0
    samples: list[str] = field(default_factory=list)

    @property
    def kind(self) -> str:
        t = self.type.upper()
        if t.startswith(NUMERIC_TYPES):
            return "number"
        if t.startswith(TEMPORAL_TYPES):
            return "date"
        if t == "BOOLEAN":
            return "boolean"
        return "text"


@dataclass
class Table:
    name: str
    description: str
    row_count: int
    columns: list[Column]


@dataclass
class QueryResult:
    columns: list[dict]       # [{"name": ..., "type": ..., "kind": ...}]
    rows: list[list]          # JSON-safe values
    truncated: bool
    elapsed_ms: float


@dataclass
class Dataset:
    id: str
    title: str
    kind: str                 # "sample" or "upload"
    conn: duckdb.DuckDBPyConnection
    tables: list[Table]
    suggestions: list[str] = field(default_factory=list)
    created: float = field(default_factory=time.time)
    last_used: float = field(default_factory=time.time)

    # ------------------------------------------------------------------ schema for the UI and the LLM
    def summary(self) -> dict:
        return {
            "id": self.id, "title": self.title, "kind": self.kind, "suggestions": self.suggestions,
            "tables": [{
                "name": t.name, "description": t.description, "rows": t.row_count,
                "columns": [{"name": c.name, "type": c.type, "kind": c.kind, "description": c.description,
                             "null_pct": c.null_pct, "distinct": c.distinct, "samples": c.samples}
                            for c in t.columns],
            } for t in self.tables],
        }

    def table_names(self) -> set[str]:
        return {t.name.lower() for t in self.tables}

    def schema_text(self) -> str:
        """Compact schema description given to the model: types, meanings and example values."""
        parts = []
        for t in self.tables:
            head = f"TABLE {t.name} ({t.row_count} rows)"
            if t.description:
                head += f" -- {t.description}"
            lines = [head]
            for c in t.columns:
                line = f"  {c.name} {c.type}"
                notes = []
                if c.description:
                    notes.append(c.description)
                if c.samples:
                    notes.append("e.g. " + ", ".join(c.samples[:4]))
                if c.null_pct >= 1:
                    notes.append(f"{c.null_pct:.0f}% NULL")
                if notes:
                    line += " -- " + "; ".join(notes)
                lines.append(line)
            parts.append("\n".join(lines))
        return "\n\n".join(parts)

    # ------------------------------------------------------------------ execution
    def execute(self, sql: str, max_rows: int = 1000, timeout_s: float = 10.0) -> QueryResult:
        self.last_used = time.time()
        cur = self.conn.cursor()
        timer = threading.Timer(timeout_s, cur.interrupt)
        start = time.perf_counter()
        timer.start()
        try:
            cur.execute(sql)
            desc = cur.description or []
            fetched = cur.fetchmany(max_rows + 1)
        except duckdb.InterruptException as exc:
            raise QueryError(f"Query took longer than {timeout_s:.0f} seconds and was stopped.") from exc
        except duckdb.Error as exc:
            raise QueryError(_clean_error(str(exc))) from exc
        finally:
            timer.cancel()
            cur.close()
        elapsed = (time.perf_counter() - start) * 1000
        columns = [{"name": d[0], "type": str(d[1]), "kind": Column(d[0], str(d[1])).kind} for d in desc]
        rows = [[_json_value(v) for v in r] for r in fetched[:max_rows]]
        return QueryResult(columns, rows, len(fetched) > max_rows, round(elapsed, 1))


def _clean_error(msg: str) -> str:
    msg = msg.strip().splitlines()[0] if msg.strip() else "Unknown database error"
    return msg[:400]


def _json_value(v):
    if v is None or isinstance(v, (bool, int, str)):
        return v
    if isinstance(v, float):
        return v if v == v else None  # NaN -> None
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (datetime, date, dtime)):
        return v.isoformat()
    if isinstance(v, timedelta):
        return str(v)
    if isinstance(v, (bytes, bytearray)):
        return "<binary>"
    if isinstance(v, uuid.UUID):
        return str(v)
    return str(v)


# --------------------------------------------------------------------------- loading

def _new_conn() -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect(":memory:", config={"threads": 2, "memory_limit": "256MB"})
    return conn


def _lock(conn: duckdb.DuckDBPyConnection) -> None:
    """After loading: no file system or network access, and settings can no longer be changed."""
    conn.execute("SET enable_external_access = false")
    conn.execute("SET lock_configuration = true")


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def profile_table(conn: duckdb.DuckDBPyConnection, name: str, descriptions: dict[str, str] | None = None,
                  description: str = "") -> Table:
    q = _quote(name)
    info = conn.execute(f"DESCRIBE {q}").fetchall()
    total = conn.execute(f"SELECT COUNT(*) FROM {q}").fetchone()[0]
    cols = []
    for col_name, col_type, *_ in info:
        c = Column(col_name, str(col_type), (descriptions or {}).get(col_name, ""))
        cq = _quote(col_name)
        nulls, distinct = conn.execute(
            f"SELECT COUNT(*) - COUNT({cq}), approx_count_distinct({cq}) FROM {q}").fetchone()
        c.null_pct = round(100 * nulls / total, 1) if total else 0.0
        c.distinct = int(distinct or 0)
        if c.kind in ("number", "date"):
            lo, hi = conn.execute(f"SELECT MIN({cq}), MAX({cq}) FROM {q}").fetchone()
            if lo is not None:
                c.samples = [f"min {_json_value(lo)}", f"max {_json_value(hi)}"]
        elif c.kind == "boolean":
            c.samples = ["true", "false"]
        else:
            top = conn.execute(
                f"SELECT {cq}, COUNT(*) n FROM {q} WHERE {cq} IS NOT NULL GROUP BY 1 ORDER BY n DESC, 1 LIMIT 6"
            ).fetchall()
            c.samples = [str(v)[:40] for v, _ in top]
        cols.append(c)
    return Table(name, description, int(total), cols)


def load_sample(sample: SampleDataset) -> Dataset:
    conn = _new_conn()
    with tempfile.TemporaryDirectory() as tmp:
        for t in sample.tables:
            path = Path(tmp) / f"{t.name}.csv"
            with path.open("w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                w.writerow([c[0] for c in t.columns])
                for r in t.rows:
                    w.writerow(["" if v is None else (v.isoformat() if isinstance(v, (date, datetime)) else v)
                                for v in r])
            types = ", ".join(f"'{c[0]}': '{c[1]}'" for c in t.columns)
            conn.execute(f"CREATE TABLE {_quote(t.name)} AS SELECT * FROM read_csv(?, header = true, "
                         f"columns = {{{types}}}, nullstr = '')", [str(path)])
    tables = [profile_table(conn, t.name, {c[0]: c[2] for c in t.columns}, t.description) for t in sample.tables]
    _lock(conn)
    return Dataset(sample.id, sample.title, "sample", conn, tables, list(sample.suggestions))


def _table_name(raw: str, taken: set[str]) -> str:
    name = re.sub(r"[^0-9a-zA-Z]+", "_", Path(raw).stem).strip("_").lower() or "data"
    if name[0].isdigit():
        name = "t_" + name
    name = name[:40]
    base, i = name, 2
    while name in taken:
        name = f"{base}_{i}"
        i += 1
    taken.add(name)
    return name


def _xlsx_to_csvs(data: bytes, tmp: Path, max_rows: int) -> list[tuple[str, Path]]:
    from openpyxl import load_workbook

    try:
        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as exc:  # corrupt or not really an Excel file
        raise DatasetError("Could not read this Excel file. Save it again as .xlsx or export it as CSV.") from exc
    out = []
    for ws in wb.worksheets[:5]:
        rows = ws.iter_rows(values_only=True)
        header = None
        for r in rows:
            if r and any(v not in (None, "") for v in r):
                header = r
                break
        if header is None:
            continue
        width = max(i + 1 for i, v in enumerate(header) if v not in (None, ""))
        names, seen = [], set()
        for i, v in enumerate(header[:width]):
            n = re.sub(r"\s+", " ", str(v).strip()) if v not in (None, "") else f"column_{i + 1}"
            while n in seen:
                n += "_2"
            seen.add(n)
            names.append(n)
        path = tmp / f"sheet_{len(out)}.csv"
        count = 0
        with path.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(names)
            for r in rows:
                vals = list(r[:width]) + [None] * (width - len(r[:width]))
                if all(v in (None, "") for v in vals):
                    continue
                count += 1
                if count > max_rows:
                    raise DatasetError(f"Sheet '{ws.title}' has more than {max_rows:,} rows.")
                w.writerow(["" if v is None else (v.isoformat() if isinstance(v, (date, datetime)) else v)
                            for v in vals])
        out.append((ws.title, path))
    wb.close()
    if not out:
        raise DatasetError("The workbook has no sheets with data.")
    return out


def load_upload(filename: str, data: bytes, max_rows: int = 200_000) -> Dataset:
    suffix = Path(filename).suffix.lower()
    if suffix not in (".csv", ".xlsx"):
        raise DatasetError("Please upload a .csv or .xlsx file.")
    if not data.strip():
        raise DatasetError("The file is empty.")
    conn = _new_conn()
    taken: set[str] = set()
    names = []
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        if suffix == ".csv":
            path = tmp_path / "upload.csv"
            path.write_bytes(data)
            sources = [(filename, path)]
        else:
            sources = _xlsx_to_csvs(data, tmp_path, max_rows)
        for label, path in sources:
            name = _table_name(label, taken)
            try:
                conn.execute(f"CREATE TABLE {_quote(name)} AS SELECT * FROM read_csv_auto(?, header = true, "
                             f"sample_size = 20000)", [str(path)])
            except duckdb.Error as exc:
                raise DatasetError(f"Could not read '{label}': {_clean_error(str(exc))}") from exc
            n = conn.execute(f"SELECT COUNT(*) FROM {_quote(name)}").fetchone()[0]
            if n == 0:
                raise DatasetError(f"'{label}' has a header but no rows.")
            if n > max_rows:
                raise DatasetError(f"'{label}' has {n:,} rows; the limit is {max_rows:,}.")
            names.append((name, label))
    tables = [profile_table(conn, n, description=f"Uploaded from {label}") for n, label in names]
    _lock(conn)
    title = Path(filename).stem[:60] or "Your data"
    return Dataset("u_" + uuid.uuid4().hex[:16], title, "upload", conn, tables)


# --------------------------------------------------------------------------- registry

class DatasetRegistry:
    """Sample datasets (shared, read-only) plus uploads kept in memory with LRU and time-based eviction."""

    def __init__(self, max_uploads: int = 25, ttl_min: int = 60):
        self.samples: dict[str, Dataset] = {}
        self.uploads: OrderedDict[str, Dataset] = OrderedDict()
        self.max_uploads = max_uploads
        self.ttl_s = ttl_min * 60
        self.lock = threading.Lock()

    def load_samples(self, ids: list[str] | None = None) -> None:
        for sid in ids or list(BUILDERS):
            self.samples[sid] = load_sample(BUILDERS[sid]())

    def add_upload(self, ds: Dataset) -> None:
        with self.lock:
            self._evict()
            self.uploads[ds.id] = ds
            while len(self.uploads) > self.max_uploads:
                _, old = self.uploads.popitem(last=False)
                old.conn.close()

    def get(self, dataset_id: str) -> Dataset:
        if dataset_id in self.samples:
            return self.samples[dataset_id]
        with self.lock:
            self._evict()
            ds = self.uploads.get(dataset_id)
            if ds is None:
                raise KeyError(dataset_id)
            self.uploads.move_to_end(dataset_id)
            ds.last_used = time.time()
            return ds

    def _evict(self) -> None:
        now = time.time()
        for key in [k for k, d in self.uploads.items() if now - d.last_used > self.ttl_s]:
            self.uploads.pop(key).conn.close()
