"""Static checks on model-written SQL before it runs.

This is one of three safety layers. The others: the dataset's DuckDB database has file and
network access switched off with its configuration locked, and every query has a timeout
and a row limit.
"""

from __future__ import annotations

from dataclasses import dataclass

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

ALLOWED_ROOTS = (exp.Select, exp.Union, exp.Intersect, exp.Except)

FORBIDDEN_NODES = tuple(
    getattr(exp, name) for name in (
        "Insert", "Update", "Delete", "Drop", "Create", "Alter", "Command", "Pragma", "Set", "Copy",
        "Attach", "Detach", "Use", "Transaction", "Commit", "Rollback", "Merge", "TruncateTable",
        "LoadData", "Grant", "Install", "Export",
    ) if hasattr(exp, name)
)

# Functions that read files, query other tables by name, or expose system state.
BLOCKED_FUNCTION_PREFIXES = ("read_", "glob", "duckdb_", "pragma_", "sniff_", "parquet_", "query",
                             "getenv", "current_setting", "load_", "install_", "export_", "import_")
BLOCKED_FUNCTION_CLASSES = tuple(getattr(exp, n) for n in ("ReadCSV", "ReadParquet") if hasattr(exp, n))


class SQLRejected(ValueError):
    """The SQL breaks a safety rule. The message is fed back to the model so it can fix the query."""


@dataclass
class CheckedSQL:
    sql: str            # what will run (row limit applied)
    display_sql: str    # the query as written, for people
    tables: list[str]


def check_sql(sql: str, allowed_tables: set[str], max_rows: int = 1000) -> CheckedSQL:
    text = (sql or "").strip().rstrip(";").strip()
    if not text:
        raise SQLRejected("The query is empty.")
    try:
        statements = [s for s in sqlglot.parse(text, read="duckdb") if s is not None]
    except ParseError as exc:
        raise SQLRejected(f"The SQL could not be parsed: {str(exc).splitlines()[0][:200]}") from exc
    if len(statements) != 1:
        raise SQLRejected("Write exactly one SQL statement.")
    tree = statements[0]
    if not isinstance(tree, ALLOWED_ROOTS):
        raise SQLRejected("Only read-only SELECT queries are allowed.")

    for node in tree.walk():
        if isinstance(node, FORBIDDEN_NODES):
            raise SQLRejected(f"{type(node).__name__.upper()} is not allowed; only read data with SELECT.")
        if isinstance(node, BLOCKED_FUNCTION_CLASSES):
            raise SQLRejected("Reading files is not allowed; query the dataset's tables.")
        if isinstance(node, exp.Func):
            fname = (node.name if isinstance(node, exp.Anonymous) else node.sql_name()).lower()
            if fname.startswith(BLOCKED_FUNCTION_PREFIXES):
                raise SQLRejected(f"The function {fname}() is not allowed.")

    cte_names = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}
    used = []
    for t in tree.find_all(exp.Table):
        name = t.name
        if not name:
            raise SQLRejected("Table functions are not allowed; query the dataset's tables by name.")
        if t.args.get("db") or t.args.get("catalog"):
            raise SQLRejected(f"Use the table name only, without a schema: {name}.")
        low = name.lower()
        if low in cte_names:
            continue
        if low not in allowed_tables:
            raise SQLRejected(f"Table '{name}' does not exist. Available tables: {', '.join(sorted(allowed_tables))}.")
        if low not in used:
            used.append(low)

    if isinstance(tree, exp.Select) and tree.args.get("limit") is None:
        tree = tree.limit(max_rows + 1)
    runnable = tree.sql(dialect="duckdb")
    return CheckedSQL(runnable, text, used)
