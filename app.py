"""ChatBI 数据分析 Agent — Streamlit 界面：对话 + 人工审批 + 图表 + 成本看板。"""
import os

import altair as alt
import pandas as pd
import requests
import streamlit as st

API_BASE = os.getenv("API_BASE", "http://localhost:8000")

st.set_page_config(page_title="ChatBI 数据分析 Agent", page_icon="📊", layout="wide")
st.title("📊 ChatBI 数据分析 Agent")
st.caption("自然语言问数据 → SQL 生成 → 安全校验 → 报错自修复 → 敏感查询人工审批 → 结论 + 图表")


def api_get(path: str) -> dict:
    return requests.get(f"{API_BASE}{path}", timeout=10).json()


def api_post(path: str, payload: dict) -> dict:
    return requests.post(f"{API_BASE}{path}", json=payload, timeout=180).json()


def render_sidebar() -> None:
    with st.sidebar:
        st.header("💰 Token 成本看板")
        try:
            cost = api_get("/api/cost")
            cols = st.columns(2)
            cols[0].metric("调用次数", cost["call_count"])
            cols[1].metric("总 Token", f"{cost['total_tokens']:,}")
            st.caption(f"输入 {cost['prompt_tokens']:,} / 输出 {cost['completion_tokens']:,}")
            st.progress(min(cost["total_tokens"] / cost["budget"], 1.0),
                        text=f"预算用量 {cost['total_tokens']:,} / {cost['budget']:,}")
        except requests.RequestException:
            st.error("后端服务未启动，请先运行 `uvicorn api:app`")

        try:
            with st.expander("🗄️ 数据库 Schema"):
                st.code(api_get("/api/schema")["schema"], language="sql")
        except requests.RequestException:
            pass

        st.info("敏感字段（如 phone）或低置信度查询会**暂停并等待人工审批**，"
                "这是演示 Human-in-the-Loop 的设计。")


def render_chart(chart: dict, columns: list[str], rows: list[list]) -> None:
    chart_type = (chart or {}).get("chart_type", "none")
    if chart_type not in ("bar", "line", "pie"):
        return
    x, y = chart.get("x"), chart.get("y")
    if x not in columns or y not in columns:
        return
    df = pd.DataFrame(rows, columns=columns)
    df[y] = pd.to_numeric(df[y], errors="coerce")
    df = df.dropna(subset=[y])
    if df.empty:
        return
    if chart_type == "pie":
        pie = alt.Chart(df).mark_arc().encode(
            theta=alt.Theta(y, type="quantitative"), color=alt.Color(x, type="nominal"))
        st.altair_chart(pie, use_container_width=True)
    elif chart_type == "line":
        st.line_chart(df.set_index(x)[y])
    else:
        st.bar_chart(df.set_index(x)[y])
    st.caption(chart.get("title", ""))


def render_result(result: dict) -> None:
    status = result.get("status")
    if status == "pending_approval":
        interrupt = result.get("interrupt") or {}
        st.warning(f"⏸️ 该查询需要人工审批：{interrupt.get('reason', '')}")
        st.code(interrupt.get("sql", ""), language="sql")
        col_approve, col_reject = st.columns(2)
        thread_id = result["thread_id"]
        if col_approve.button("✅ 批准执行", key=f"approve-{thread_id}"):
            st.session_state.pending_result = api_post(
                "/api/approve", {"thread_id": thread_id, "approved": True})
        elif col_reject.button("❌ 拒绝执行", key=f"reject-{thread_id}"):
            st.session_state.pending_result = api_post(
                "/api/approve", {"thread_id": thread_id, "approved": False})
        return
    if status == "failed":
        st.error(result.get("answer", "查询失败"))
        return

    st.markdown(result.get("answer", ""))
    render_chart(result.get("chart", {}), result.get("columns", []), result.get("rows", []))
    with st.expander(f"查看 SQL（生成 {result.get('attempts', 1)} 次）"):
        st.code(result.get("sql", ""), language="sql")
    if result.get("rows"):
        st.dataframe(pd.DataFrame(result["rows"], columns=result["columns"]), use_container_width=True)
    with st.expander("执行轨迹"):
        st.json(result.get("steps", []))


def main() -> None:
    render_sidebar()
    if "messages" not in st.session_state:
        st.session_state.messages = []

    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            if message["role"] == "user":
                st.markdown(message["content"])
            else:
                render_result(message["result"])

    # 审批按钮点击后刷新该条消息的结果
    if "pending_result" in st.session_state:
        for message in reversed(st.session_state.messages):
            if message["role"] == "assistant" and message["result"].get("thread_id") \
                    == st.session_state.pending_result.get("thread_id"):
                message["result"] = st.session_state.pending_result
                break
        del st.session_state.pending_result
        st.rerun()

    question = st.chat_input("试试问：每个月的订单量趋势是怎样的？")
    if not question:
        return
    st.session_state.messages.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(question)
    with st.chat_message("assistant"):
        with st.spinner("分析中：生成 SQL → 校验 → 执行..."):
            try:
                result = api_post("/api/chat", {"question": question})
            except requests.RequestException as error:
                result = {"thread_id": "", "status": "failed", "answer": f"后端服务不可用: {error}"}
        st.session_state.messages.append({"role": "assistant", "result": result})
        st.rerun()


if __name__ == "__main__":
    main()
