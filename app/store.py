"""Question history and pinned charts. SQLite locally, PostgreSQL in production (DATABASE_URL)."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    Column,
    DateTime,
    Float,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    create_engine,
    delete,
    insert,
    select,
)

metadata = MetaData()

history = Table(
    "history", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("session_id", String(64), index=True, nullable=False),
    Column("conversation_id", String(64), index=True, nullable=False),
    Column("dataset_id", String(64), nullable=False),
    Column("question", Text, nullable=False),
    Column("sql", Text),
    Column("status", String(20), nullable=False),
    Column("latency_ms", Float),
    Column("cost_usd", Float),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

pins = Table(
    "pins", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("session_id", String(64), index=True, nullable=False),
    Column("dataset_id", String(64), nullable=False),
    Column("dataset_title", String(120), nullable=False),
    Column("question", Text, nullable=False),
    Column("sql", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)


def _normalise_url(url: str) -> str:
    # Hosts often hand out postgres:// URLs; SQLAlchemy needs the driver named.
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


class Store:
    def __init__(self, url: str):
        url = _normalise_url(url)
        kwargs = {"pool_pre_ping": True} if url.startswith("postgresql") else {}
        self.engine = create_engine(url, **kwargs)
        metadata.create_all(self.engine)

    def add_history(self, *, session_id: str, conversation_id: str, dataset_id: str, question: str, sql: str | None,
                    status: str, latency_ms: float, cost_usd: float) -> None:
        with self.engine.begin() as c:
            c.execute(insert(history).values(
                session_id=session_id, conversation_id=conversation_id, dataset_id=dataset_id, question=question,
                sql=sql, status=status, latency_ms=latency_ms, cost_usd=cost_usd, created_at=datetime.now(UTC)))

    def conversation(self, conversation_id: str, dataset_id: str, limit: int = 3) -> list[dict]:
        """Earlier answered questions in this conversation, oldest first."""
        q = (select(history.c.question, history.c.sql)
             .where(history.c.conversation_id == conversation_id, history.c.dataset_id == dataset_id,
                    history.c.status == "answered")
             .order_by(history.c.id.desc()).limit(limit))
        with self.engine.connect() as c:
            rows = c.execute(q).all()
        return [{"question": r.question, "sql": r.sql} for r in reversed(rows)]

    def add_pin(self, *, session_id: str, dataset_id: str, dataset_title: str, question: str, sql: str) -> int:
        with self.engine.begin() as c:
            res = c.execute(insert(pins).values(session_id=session_id, dataset_id=dataset_id,
                                                dataset_title=dataset_title, question=question, sql=sql,
                                                created_at=datetime.now(UTC)))
            return int(res.inserted_primary_key[0])

    def list_pins(self, session_id: str) -> list[dict]:
        q = select(pins).where(pins.c.session_id == session_id).order_by(pins.c.id)
        with self.engine.connect() as c:
            return [dict(r._mapping) for r in c.execute(q).all()]

    def delete_pin(self, session_id: str, pin_id: int) -> bool:
        with self.engine.begin() as c:
            res = c.execute(delete(pins).where(pins.c.id == pin_id, pins.c.session_id == session_id))
            return res.rowcount > 0
