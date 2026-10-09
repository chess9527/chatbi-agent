"""SQLite 只读访问与 Schema 提取。"""
import sqlite3
from pathlib import Path

from chatbi.config import get_settings


def get_connection() -> sqlite3.Connection:
    """以只读模式打开数据库，从连接层杜绝写操作。"""
    path = Path(get_settings().db_path)
    if not path.exists():
        raise FileNotFoundError(f"数据库不存在: {path}，请先运行 python scripts/init_db.py")
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def fetch_schema(sample_rows: int = 3) -> str:
    """提取所有表的列定义与样例数据，注入 Prompt 供 LLM 生成 SQL。"""
    conn = get_connection()
    try:
        tables = [r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        parts = []
        for table in tables:
            cols = conn.execute(f"PRAGMA table_info({table})").fetchall()
            col_lines = ",\n".join(
                f"  {c['name']} {c['type']}" + ("  -- 主键" if c["pk"] else "") for c in cols)
            samples = conn.execute(f"SELECT * FROM {table} LIMIT {sample_rows}").fetchall()
            sample_lines = "\n".join(str(tuple(row)) for row in samples)
            parts.append(f"表 {table}:\nCREATE TABLE {table} (\n{col_lines}\n);\n样例数据:\n{sample_lines}")
        return "\n\n".join(parts)
    finally:
        conn.close()


def execute_query(sql: str, max_rows: int) -> tuple[list[str], list[list]]:
    """执行只读查询，返回 (列名, 行数据)，行数超限时截断。"""
    conn = get_connection()
    try:
        cursor = conn.execute(sql)
        columns = [d[0] for d in cursor.description] if cursor.description else []
        rows = cursor.fetchmany(max_rows + 1)
        truncated = len(rows) > max_rows
        if truncated:
            rows = rows[:max_rows]
        data = [list(row) for row in rows]
        if truncated:
            data.append(["...（已截断，仅显示前 " f"{max_rows} 行）"] + [""] * (len(columns) - 1))
        return columns, data
    finally:
        conn.close()
