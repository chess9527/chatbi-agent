"""SQL 安全校验：只放行单条只读 SELECT，强制行数上限，识别敏感列。"""
import re
from dataclasses import dataclass

import sqlparse

# 禁止出现的关键词（写操作 / 危险语句；REPLACE 同时是内置函数名，不列入避免误伤）
BANNED_KEYWORDS = {
    "INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE", "TRIGGER",
    "ATTACH", "DETACH", "PRAGMA", "GRANT", "REVOKE", "VACUUM", "MERGE",
}


@dataclass
class SecurityReport:
    ok: bool
    sql: str = ""
    reason: str = ""
    needs_approval: bool = False


def _first_keyword(sql: str) -> str:
    """取 SQL 第一个有意义的关键字（跳过空白/注释/标点）。"""
    tokens = (t for t in sqlparse.parse(sql)[0].flatten()
              if not t.is_whitespace and not (t.ttype and t.ttype in sqlparse.tokens.Comment)
              and t.ttype is not sqlparse.tokens.Punctuation)
    token = next(tokens, None)
    return token.normalized.upper() if token else ""


def validate_sql(raw_sql: str, sensitive_columns: list[str], max_rows: int) -> SecurityReport:
    """校验 LLM 生成的 SQL：非法直接拒绝，合法但带敏感列则要求人工审批。"""
    sql = (raw_sql or "").strip()
    if sql.endswith(";"):
        sql = sql[:-1].strip()
    if not sql:
        return SecurityReport(ok=False, reason="SQL 为空")

    # 多语句：一次只允许执行一条
    if ";" in sql:
        return SecurityReport(ok=False, reason="不允许一次执行多条 SQL")

    # 首关键字必须是 SELECT / WITH（CTE）
    if _first_keyword(sql) not in ("SELECT", "WITH"):
        return SecurityReport(ok=False, reason="只允许 SELECT 查询语句")

    # 禁用关键词逐 token 检查（Keyword 层级同时覆盖 DML/DDL，字符串字面量不受影响）
    for token in sqlparse.parse(sql)[0].flatten():
        if token.ttype and token.ttype in sqlparse.tokens.Keyword \
                and token.normalized.upper() in BANNED_KEYWORDS:
            return SecurityReport(ok=False, reason=f"检测到禁止关键词: {token.normalized.upper()}")

    # 行数上限：无 LIMIT 则补上，超出上限则收敛
    limit_match = re.search(r"\bLIMIT\s+(\d+)\b", sql, re.IGNORECASE)
    if limit_match:
        if int(limit_match.group(1)) > max_rows:
            sql = sql[:limit_match.start()] + f"LIMIT {max_rows}" + sql[limit_match.end():]
    else:
        sql = f"{sql}\nLIMIT {max_rows}"

    # 敏感列：合法但需人工审批（Human-in-the-Loop）
    hit = [col for col in sensitive_columns if re.search(rf"\b{re.escape(col)}\b", sql, re.IGNORECASE)]
    if hit:
        return SecurityReport(ok=True, sql=sql, needs_approval=True,
                              reason=f"查询涉及敏感字段: {', '.join(hit)}")

    return SecurityReport(ok=True, sql=sql)
