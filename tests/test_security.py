"""SQL 安全校验测试：只放行单条只读 SELECT，验证 LIMIT 收敛与敏感列识别。"""
from chatbi.security import validate_sql

SENSITIVE = ["phone", "email", "id_card", "salary", "address"]
MAX_ROWS = 200


def validate(sql: str):
    return validate_sql(sql, SENSITIVE, MAX_ROWS)


def test_reject_empty_sql():
    assert not validate("").ok
    assert not validate("   ").ok


def test_reject_multi_statement():
    report = validate("SELECT 1; DROP TABLE users")
    assert not report.ok
    assert "多条" in report.reason


def test_reject_non_select():
    for sql in ["UPDATE users SET name = 'x'",
                "DELETE FROM users",
                "DROP TABLE users",
                "INSERT INTO users VALUES (1)"]:
        assert not validate(sql).ok, sql


def test_allow_select_and_cte():
    assert validate("SELECT * FROM users").ok
    assert validate("WITH t AS (SELECT * FROM users) SELECT * FROM t").ok


def test_reject_banned_keyword_in_cte():
    report = validate("WITH t AS (DELETE FROM users RETURNING *) SELECT * FROM t")
    assert not report.ok
    assert "DELETE" in report.reason


def test_reject_pragma():
    assert not validate("PRAGMA table_list").ok


def test_keyword_inside_string_literal_allowed():
    # 字符串字面量中出现危险词不误伤（ttype 不是 Keyword）
    report = validate("SELECT * FROM users WHERE name = 'DROP TABLE'")
    assert report.ok


def test_limit_appended_when_missing():
    report = validate("SELECT name FROM users")
    assert report.ok
    assert report.sql.rstrip().endswith("LIMIT 200")


def test_limit_clamped_when_over_max():
    report = validate("SELECT name FROM users LIMIT 10000")
    assert report.ok
    assert "LIMIT 200" in report.sql


def test_limit_within_max_unchanged():
    report = validate("SELECT name FROM users LIMIT 10")
    assert report.ok
    assert "LIMIT 10" in report.sql


def test_sensitive_column_requires_approval():
    report = validate("SELECT name, phone FROM users")
    assert report.ok
    assert report.needs_approval
    assert "phone" in report.reason


def test_normal_query_no_approval():
    report = validate("SELECT COUNT(*) FROM orders")
    assert report.ok
    assert not report.needs_approval
