"""LangGraph 编排：生成 SQL → 安全校验 → 自修复循环 → 人工审批(HITL) → 执行 → 总结。"""
import json
import re
import sqlite3
import time
import uuid
from typing import Any, Literal

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from openai import APIError

from chatbi.config import Settings, get_settings
from chatbi.database import execute_query, fetch_schema
from chatbi.llm import CircuitOpenError, LLMClient
from chatbi.security import validate_sql
from chatbi.state import AgentState

LLM_ERRORS = (CircuitOpenError, APIError, ValueError)


def _extract_json(text: str) -> dict:
    """解析模型输出，容忍 Markdown 代码围栏。"""
    match = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text.strip(), re.DOTALL)
    payload = match.group(1) if match else text.strip()
    data = json.loads(payload)
    if not isinstance(data, dict):
        raise ValueError("模型输出不是 JSON 对象")
    return data


def _step(steps: list[dict], name: str, **fields: Any) -> list[dict]:
    """追加一条执行轨迹。"""
    return steps + [{"step": name, **fields}]


def _sql_gen_messages(schema_text: str, question: str) -> list[dict]:
    system = (
        "你是资深数据分析工程师，把用户问题转成 SQLite 查询。\n"
        f"可用表结构：\n{schema_text}\n\n"
        "规则：\n"
        "1. 只生成一条只读 SELECT（允许 WITH/CTE），禁止任何写操作；\n"
        "2. 只能使用表结构中真实存在的表和列，禁止编造；\n"
        "3. 只返回回答问题所需的最少列，不要附加 id 或其他无关列；\n"
        "4. 关联查询只统计实际存在的记录（INNER JOIN），结果中不得出现 NULL 行；\n"
        "5. 金额聚合用 ROUND(..., 2)；比例类问题默认输出百分比并保留两位小数；\n"
        "6. 结果可能很多时主动加 LIMIT；\n"
        '7. 只输出 JSON：{"sql": "...", "confidence": "high 或 low", "explanation": "一句话思路"}，'
        "无法确定表结构时 confidence 填 low。"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": question}]


def _repair_messages(schema_text: str, state: AgentState) -> list[dict]:
    system = (
        "你是 SQL 修复专家。上一次生成的 SQL 存在问题，请修复它。\n"
        f"可用表结构：\n{schema_text}\n\n"
        "规则与之前相同：单条只读 SELECT，只用真实存在的列。\n"
        '只输出 JSON：{"sql": "...", "confidence": "high 或 low", "explanation": "修复思路一句话"}'
    )
    payload = json.dumps({
        "用户问题": state["question"],
        "上次SQL": state.get("sql") or "（未生成有效 SQL）",
        "问题": state.get("error", ""),
    }, ensure_ascii=False)
    return [{"role": "system", "content": system}, {"role": "user", "content": payload}]


def _summary_messages(state: AgentState, sample_rows: list[list]) -> list[dict]:
    system = (
        "你是数据分析师，基于查询结果回答用户问题。\n"
        '只输出 JSON：{"answer": "结论性回答，必须引用结果中的关键数字，不超过150字", '
        '"chart": {"chart_type": "bar|line|pie|none", "x": "x轴列名", "y": "y轴列名", "title": "图表标题"}}\n'
        "仅当结果适合可视化（分组对比/时间趋势/占比）时给出 chart，否则 chart_type 为 none；"
        "x/y 必须使用结果列名。"
    )
    payload = json.dumps({
        "用户问题": state["question"],
        "SQL": state["sql"],
        "列名": state.get("columns", []),
        "结果": sample_rows,
    }, ensure_ascii=False)
    return [{"role": "system", "content": system}, {"role": "user", "content": payload}]


def build_graph(llm: LLMClient, schema_text: str, settings: Settings):
    """构建 ChatBI Agent 的状态图。"""

    def over_budget() -> bool:
        return llm.total_tokens >= settings.session_token_budget

    def generate_sql(state: AgentState) -> dict:
        if over_budget():
            # 直接置为最大尝试次数，让路由立刻走向 fail，不再进入校验/修复环节
            return {"status": "failed",
                    "error": "Token 预算已耗尽，服务已停止调用模型，请稍后再试。",
                    "attempts": settings.sql_max_attempts}
        start = time.perf_counter()
        try:
            response = llm.chat(_sql_gen_messages(schema_text, state["question"]), json_mode=True)
            data = _extract_json(response.content)
        except LLM_ERRORS as error:
            return {"error": f"模型调用失败: {error}",
                    "attempts": state.get("attempts", 0) + 1,
                    "steps": _step(state.get("steps", []), "generate_sql", error=str(error))}
        return {"sql": str(data.get("sql", "")).strip(),
                "confidence": str(data.get("confidence", "low")),
                "attempts": state.get("attempts", 0) + 1,
                "steps": _step(state.get("steps", []), "generate_sql",
                               latency_ms=round((time.perf_counter() - start) * 1000),
                               prompt_tokens=response.prompt_tokens,
                               completion_tokens=response.completion_tokens,
                               detail=str(data.get("explanation", "")))}

    def validate_sql_node(state: AgentState) -> dict:
        if state.get("status") == "failed":  # 上游已短路（如预算耗尽），直接放行到 fail
            return {}
        report = validate_sql(state.get("sql", ""), settings.sensitive_columns, settings.max_rows)
        if not report.ok:
            return {"error": report.reason,
                    "attempts": state.get("attempts", 0) + 1,
                    "steps": _step(state.get("steps", []), "validate_sql",
                                   passed=False, reason=report.reason)}
        # 合法但带敏感列 / 低置信度 → 转人工审批
        if report.needs_approval:
            reason = report.reason
        elif state.get("confidence") == "low":
            reason = "模型对生成 SQL 的置信度较低，建议人工确认"
        else:
            reason = ""
        return {"error": "", "sql": report.sql,
                "needs_approval": bool(reason),
                "interrupt_reason": reason,
                "steps": _step(state.get("steps", []), "validate_sql",
                               passed=True, needs_approval=bool(reason))}

    def approval(state: AgentState) -> dict:
        """Human-in-the-Loop：挂起等待人工审批。"""
        decision = interrupt({"type": "approval_required",
                              "sql": state["sql"],
                              "reason": state.get("interrupt_reason", "")})
        return {"approved": bool(decision),
                "status": "" if decision else "rejected",
                "steps": _step(state.get("steps", []), "approval", approved=bool(decision))}

    def execute_sql(state: AgentState) -> dict:
        if state.get("status") == "rejected":
            return {"answer": "该查询已被人工审批拒绝，已停止执行。",
                    "steps": _step(state.get("steps", []), "execute_sql", skipped=True)}
        start = time.perf_counter()
        try:
            columns, rows = execute_query(state["sql"], settings.max_rows)
        except sqlite3.Error as error:
            return {"error": f"SQL 执行失败: {error}",
                    "attempts": state.get("attempts", 0) + 1,
                    "steps": _step(state.get("steps", []), "execute_sql", error=str(error))}
        return {"columns": columns, "rows": rows, "error": "",
                "steps": _step(state.get("steps", []), "execute_sql",
                               latency_ms=round((time.perf_counter() - start) * 1000),
                               row_count=len(rows))}

    def repair_sql(state: AgentState) -> dict:
        if over_budget():
            return {"status": "failed",
                    "error": "Token 预算已耗尽，服务已停止调用模型，请稍后再试。",
                    "attempts": settings.sql_max_attempts,
                    "steps": _step(state.get("steps", []), "repair_sql", skipped="budget_exhausted")}
        start = time.perf_counter()
        try:
            response = llm.chat(_repair_messages(schema_text, state), json_mode=True)
            data = _extract_json(response.content)
        except LLM_ERRORS as error:
            return {"error": f"修复时模型调用失败: {error}",
                    "steps": _step(state.get("steps", []), "repair_sql", error=str(error))}
        return {"sql": str(data.get("sql", "")).strip(),
                "confidence": str(data.get("confidence", "low")),
                "steps": _step(state.get("steps", []), "repair_sql",
                               latency_ms=round((time.perf_counter() - start) * 1000),
                               prompt_tokens=response.prompt_tokens,
                               completion_tokens=response.completion_tokens,
                               detail=str(data.get("explanation", "")))}

    def summarize(state: AgentState) -> dict:
        columns, rows = state.get("columns", []), state.get("rows", [])
        if not rows:
            return {"status": "done", "answer": "查询完成：没有符合条件的数据。", "chart": {}}
        if over_budget():
            return {"status": "done", "answer": _fallback_answer(columns, rows), "chart": {}}
        try:
            response = llm.chat(_summary_messages(state, rows[:20]), json_mode=True)
            data = _extract_json(response.content)
            chart = _safe_chart(data.get("chart"), columns)
        except LLM_ERRORS:
            chart = {}
            return {"status": "done", "answer": _fallback_answer(columns, rows), "chart": chart}
        return {"status": "done",
                "answer": str(data.get("answer", "")).strip() or _fallback_answer(columns, rows),
                "chart": chart,
                "steps": _step(state.get("steps", []), "summarize",
                               prompt_tokens=response.prompt_tokens,
                               completion_tokens=response.completion_tokens)}

    def fail(state: AgentState) -> dict:
        return {"status": "failed",
                "answer": state.get("answer")
                or f"查询失败：{state.get('error', '未知错误')}（已尝试 {state.get('attempts', 0)} 次生成）"}

    def route_after_validate(state: AgentState) -> Literal["approval", "execute", "repair", "fail"]:
        if state.get("status") == "failed":
            return "fail"
        if state.get("error"):
            return "fail" if state.get("attempts", 0) >= settings.sql_max_attempts else "repair"
        return "approval" if state.get("needs_approval") else "execute"

    def route_after_execute(state: AgentState) -> Literal["summarize", "repair", "fail", "reject"]:
        if state.get("status") == "rejected":
            return "reject"
        if state.get("error"):
            return "fail" if state.get("attempts", 0) >= settings.sql_max_attempts else "repair"
        return "summarize"

    builder = StateGraph(AgentState)
    builder.add_node("generate_sql", generate_sql)
    builder.add_node("validate_sql", validate_sql_node)
    builder.add_node("approval", approval)
    builder.add_node("execute_sql", execute_sql)
    builder.add_node("repair_sql", repair_sql)
    builder.add_node("summarize", summarize)
    builder.add_node("fail", fail)

    builder.add_edge(START, "generate_sql")
    builder.add_edge("generate_sql", "validate_sql")
    builder.add_conditional_edges("validate_sql", route_after_validate,
                                  {"approval": "approval", "execute": "execute_sql",
                                   "repair": "repair_sql", "fail": "fail"})
    builder.add_edge("repair_sql", "validate_sql")
    builder.add_edge("approval", "execute_sql")
    builder.add_conditional_edges("execute_sql", route_after_execute,
                                  {"summarize": "summarize", "repair": "repair_sql",
                                   "fail": "fail", "reject": END})
    builder.add_edge("summarize", END)
    builder.add_edge("fail", END)
    return builder.compile(checkpointer=MemorySaver())


def _safe_chart(chart: Any, columns: list[str]) -> dict:
    """图表配置合法性检查：类型与列名不合法则不绘图。"""
    if not isinstance(chart, dict):
        return {}
    if chart.get("chart_type") not in ("bar", "line", "pie"):
        return {}
    if chart.get("x") not in columns or chart.get("y") not in columns:
        return {}
    return {key: chart.get(key, "") for key in ("chart_type", "x", "y", "title")}


def _fallback_answer(columns: list[str], rows: list[list]) -> str:
    """模型不可用时的确定性兜底回答。"""
    preview = [dict(zip(columns, row)) for row in rows[:5]]
    return f"（模型服务暂不可用，展示原始数据）共 {len(rows)} 行结果：{preview}"


class ChatBIAgent:
    """对外门面：ask 发起问答，resume 恢复被审批挂起的会话。"""

    def __init__(self):
        self.settings = get_settings()
        self.llm = LLMClient(self.settings)
        self.schema_text = fetch_schema()
        self.graph = build_graph(self.llm, self.schema_text, self.settings)

    def ask(self, question: str) -> dict:
        config = {"configurable": {"thread_id": uuid.uuid4().hex}}
        initial: AgentState = {"question": question, "schema_text": self.schema_text,
                               "attempts": 0, "steps": []}
        result = self.graph.invoke(initial, config)
        return self._normalize(result, config)

    def resume(self, thread_id: str, approved: bool) -> dict:
        config = {"configurable": {"thread_id": thread_id}}
        # 校验会话存在：无效 thread 会导致图从空状态重跑，必须提前拦截
        if not self.graph.get_state(config).values:
            raise ValueError(f"会话不存在或已过期: {thread_id}")
        result = self.graph.invoke(Command(resume=bool(approved)), config)
        return self._normalize(result, config)

    def _normalize(self, result: dict, config: dict) -> dict:
        thread_id = config["configurable"]["thread_id"]
        if "__interrupt__" in result:
            state = dict(self.graph.get_state(config).values)
            interrupt_payload = result["__interrupt__"][0].value
            return {**state, "thread_id": thread_id,
                    "status": "pending_approval", "interrupt": interrupt_payload}
        return {**result, "thread_id": thread_id, "interrupt": None}
