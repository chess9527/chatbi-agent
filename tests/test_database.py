"""数据库层测试：只读连接强制生效、查询与 Schema 提取正常。"""
import sqlite3

import pytest

from chatbi.database import execute_query, fetch_schema, get_connection


def test_readonly_connection_blocks_write():
    conn = get_connection()
    try:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("CREATE TABLE hack(a INT)")
    finally:
        conn.close()


def test_execute_query_returns_columns_and_rows():
    columns, rows = execute_query("SELECT id, name FROM users ORDER BY id LIMIT 3", 200)
    assert columns == ["id", "name"]
    assert len(rows) == 3


def test_execute_query_truncates_over_limit():
    columns, rows = execute_query("SELECT id FROM orders", 10)
    assert len(rows) == 11  # 10 行数据 + 1 行截断提示
    assert any(str(row[0]).startswith("...") for row in rows)


def test_fetch_schema_contains_tables_and_samples():
    schema = fetch_schema()
    for table in ["users", "products", "orders", "order_items"]:
        assert f"表 {table}" in schema
    assert "样例数据" in schema
