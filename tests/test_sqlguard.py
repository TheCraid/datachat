import pytest

from app.sqlguard import SQLRejected, check_sql

TABLES = {"orders", "customers"}


@pytest.mark.parametrize("sql", [
    "SELECT city, SUM(amount) FROM orders GROUP BY city",
    "WITH q AS (SELECT * FROM orders) SELECT * FROM q",
    "SELECT * FROM orders o JOIN customers c USING (customer_id)",
    "SELECT city FROM orders UNION SELECT city FROM customers",
    "select date_trunc('month', order_date) m, count(*) from orders group by 1 order by 1;",
])
def test_allows_read_only_queries(sql):
    assert check_sql(sql, TABLES).sql


@pytest.mark.parametrize("sql, reason", [
    ("DROP TABLE orders", "SELECT"),
    ("DELETE FROM orders", "SELECT"),
    ("UPDATE orders SET amount = 0", "SELECT"),
    ("INSERT INTO orders VALUES (1)", "SELECT"),
    ("SELECT 1; DROP TABLE orders", "one SQL statement"),
    ("COPY orders TO 'out.csv'", "SELECT"),
    ("ATTACH 'other.db'", "SELECT"),
    ("SET threads = 1", "SELECT"),
    ("PRAGMA database_list", "SELECT"),
    ("SELECT * FROM read_csv('/etc/passwd')", "Reading files"),
    ("SELECT * FROM secrets", "does not exist"),
    ("SELECT getenv('HOME')", "getenv"),
    ("SELECT * FROM duckdb_settings()", "duckdb_settings"),
    ("SELECT * FROM generate_series(1, 5)", "Table functions"),
    ("", "empty"),
    ("SELEC city FROM orders", None),
])
def test_rejects_unsafe_or_invalid_sql(sql, reason):
    with pytest.raises(SQLRejected, match=reason):
        check_sql(sql, TABLES)


def test_adds_row_limit_but_keeps_an_existing_one():
    assert check_sql("SELECT * FROM orders", TABLES, max_rows=100).sql.endswith("LIMIT 101")
    assert check_sql("SELECT * FROM orders LIMIT 5", TABLES, max_rows=100).sql.endswith("LIMIT 5")


def test_cte_names_are_not_treated_as_unknown_tables():
    c = check_sql("WITH big AS (SELECT * FROM orders WHERE amount > 100) SELECT COUNT(*) FROM big", TABLES)
    assert c.tables == ["orders"]
