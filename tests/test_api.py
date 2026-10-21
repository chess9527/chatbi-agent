"""API 层测试：注入假 Agent，验证路由、审批与错误处理，全程离线。"""
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import api


class FakeAgent:
    """离线假 Agent：ask 返回固定结果，resume 抛会话不存在。"""

    def __init__(self):
        self.llm = SimpleNamespace(
            call_count=3, total_prompt_tokens=1000,
            total_completion_tokens=200, total_tokens=1200)

    def ask(self, question: str) -> dict:
        return {"thread_id": "fake-thread", "status": "done",
                "answer": f"已回答:{question}", "sql": "SELECT 1",
                "columns": ["1"], "rows": [[1]], "chart": {},
                "attempts": 1, "interrupt": None,
                "steps": [{"step": "generate_sql"}]}

    def resume(self, thread_id: str, approved: bool) -> dict:
        if thread_id != "pending-thread":
            raise ValueError(f"会话不存在或已过期: {thread_id}")
        return {"thread_id": thread_id, "status": "done", "answer": "审批后完成",
                "sql": "SELECT name, phone FROM users", "columns": [], "rows": [],
                "chart": {}, "attempts": 1, "interrupt": None, "steps": []}


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(api, "_agent", FakeAgent())
    return TestClient(api.app)


def test_health(client):
    assert client.get("/api/health").json() == {"status": "ok"}


def test_schema(client):
    body = client.get("/api/schema").json()
    assert "表 users" in body["schema"]


def test_chat(client):
    body = client.post("/api/chat", json={"question": "有多少订单"}).json()
    assert body["status"] == "done"
    assert body["thread_id"] == "fake-thread"
    assert body["answer"] == "已回答:有多少订单"


def test_chat_rejects_blank_question(client):
    resp = client.post("/api/chat", json={"question": "  "})
    assert resp.status_code == 422


def test_cost(client):
    body = client.get("/api/cost").json()
    assert body["total_tokens"] == 1200
    assert body["call_count"] == 3


def test_approve_unknown_thread_returns_404(client):
    resp = client.post("/api/approve", json={"thread_id": "ghost", "approved": True})
    assert resp.status_code == 404


def test_approve_pending_thread(client):
    resp = client.post("/api/approve", json={"thread_id": "pending-thread", "approved": True})
    assert resp.status_code == 200
    assert resp.json()["status"] == "done"
