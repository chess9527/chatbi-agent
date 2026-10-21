"""LangGraph 全流程离线测试：注入假 LLM，覆盖正常链路 / 自修复 / HITL / 预算保护。"""
import json
from pathlib import Path
import sys

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from langgraph.types import Command  # noqa: E402

from chatbi.config import get_settings  # noqa: E402
from chatbi.database import fetch_schema  # noqa: E402
from chatbi.graph import ChatBIAgent, build_graph  # noqa: E402
from chatbi.llm import LLMResponse  # noqa: E402


class FakeLLM:
    """离线假 LLM：按脚本依次返回响应，接口与 LLMClient 对齐。"""

    def __init__(self, scripted: list[str]):
        self.scripted = list(scripted)
        self.calls: list[dict] = []
        self.total_prompt_tokens = 0
        self.total_completion_tokens = 0

    @property
    def total_tokens(self) -> int:
        return self.total_prompt_tokens + self.total_completion_tokens

    def chat(self, messages: list[dict], json_mode: bool = False) -> LLMResponse:
        self.calls.append({"messages": messages, "json_mode": json_mode})
        self.total_prompt_tokens += 100
        self.total_completion_tokens += 10
        if not self.scripted:
            raise AssertionError("脚本耗尽：出现了未预期的 LLM 调用")
        content = self.scripted.pop(0)
        return LLMResponse(content=content, prompt_tokens=100,
                           completion_tokens=10, model="fake")


def sql_json(sql: str, confidence: str = "high") -> str:
    return json.dumps({"sql": sql, "confidence": confidence, "explanation": "测试"}, ensure_ascii=False)


def summary_json(answer: str = "测试结论") -> str:
    return json.dumps({"answer": answer, "chart": {"chart_type": "none"}}, ensure_ascii=False)


def run(graph, llm, question: str, thread_id: str) -> dict:
    initial = {"question": question, "schema_text": fetch_schema(), "attempts": 0, "steps": []}
    config = {"configurable": {"thread_id": thread_id}}
    return graph.invoke(initial, config), config


def test_happy_flow_done():
    llm = FakeLLM([sql_json("SELECT COUNT(*) FROM orders"), summary_json("共 400 个订单")])
    graph = build_graph(llm, fetch_schema(), get_settings())
    result, _ = run(graph, llm, "总共有多少订单", "t-happy")
    assert result["status"] == "done"
    assert result["rows"][0][0] == 400
    assert result["answer"] == "共 400 个订单"
    assert len(llm.calls) == 2


def test_repair_loop_recovers():
    # 第一次生成不存在的表 → 执行报错 → 自修复后成功 → 总结
    llm = FakeLLM([sql_json("SELECT * FROM ghost_table"),
                   sql_json("SELECT COUNT(*) FROM orders"),
                   summary_json("共 400 个订单")])
    graph = build_graph(llm, fetch_schema(), get_settings())
    result, _ = run(graph, llm, "有多少订单", "t-repair")
    assert result["status"] == "done"
    steps = [s["step"] for s in result["steps"]]
    assert "repair_sql" in steps
    assert "execute_sql" in steps
    assert len(llm.calls) == 3


def test_repair_exhausted_fails():
    # 修复后仍指向不存在的表 → 达到 attempts 上限进入 fail
    llm = FakeLLM([sql_json("SELECT * FROM ghost_table"),
                   sql_json("SELECT * FROM still_ghost")])
    graph = build_graph(llm, fetch_schema(), get_settings())
    result, _ = run(graph, llm, "有多少订单", "t-exhaust")
    assert result["status"] == "failed"
    assert "已尝试" in result["answer"]


def test_hitl_sensitive_column_interrupt_and_resume():
    llm = FakeLLM([sql_json("SELECT name, phone FROM users LIMIT 5"), summary_json("已查询")])
    graph = build_graph(llm, fetch_schema(), get_settings())
    result, config = run(graph, llm, "查用户的手机号", "t-hitl")
    # 含敏感列 → 挂起等待人工审批
    assert "__interrupt__" in result
    payload = result["__interrupt__"][0].value
    assert payload["type"] == "approval_required"
    assert "phone" in payload["reason"]

    resumed = graph.invoke(Command(resume=True), config)
    assert resumed["status"] == "done"
    approval_steps = [s for s in resumed["steps"] if s["step"] == "approval"]
    assert approval_steps and approval_steps[-1]["approved"] is True


def test_hitl_rejected():
    llm = FakeLLM([sql_json("SELECT name, phone FROM users LIMIT 5")])
    graph = build_graph(llm, fetch_schema(), get_settings())
    _, config = run(graph, llm, "查用户的手机号", "t-reject")
    resumed = graph.invoke(Command(resume=False), config)
    assert resumed["status"] == "rejected"
    assert "拒绝" in resumed["answer"]


def test_low_confidence_triggers_approval():
    llm = FakeLLM([sql_json("SELECT COUNT(*) FROM orders", confidence="low"), summary_json("ok")])
    graph = build_graph(llm, fetch_schema(), get_settings())
    result, config = run(graph, llm, "有多少订单", "t-lowconf")
    assert "__interrupt__" in result
    assert "置信度" in result["__interrupt__"][0].value["reason"]
    resumed = graph.invoke(Command(resume=True), config)
    assert resumed["status"] == "done"


def test_token_budget_blocks_llm_call():
    settings = get_settings().model_copy(update={"session_token_budget": 10})
    llm = FakeLLM([])
    llm.total_prompt_tokens = 100  # 预先累计 Token，模拟预算已耗尽
    graph = build_graph(llm, fetch_schema(), settings)
    result, _ = run(graph, llm, "有多少订单", "t-budget")
    assert result["status"] == "failed"
    assert "预算" in result["answer"]
    assert llm.calls == []  # 预算拦截发生在 LLM 调用之前


def test_resume_invalid_thread_raises():
    agent = ChatBIAgent()
    with pytest.raises(ValueError):
        agent.resume("no-such-thread", True)
