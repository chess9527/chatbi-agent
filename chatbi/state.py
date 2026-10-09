"""Agent 状态定义：LangGraph 全流程共享。"""
from typing import TypedDict


class AgentState(TypedDict, total=False):
    question: str            # 用户问题
    schema_text: str         # 数据库 Schema（含样例行）
    sql: str                 # 当前 SQL
    confidence: str          # 模型自评置信度 high / low
    error: str               # 校验或执行错误，空串表示无错误
    attempts: int            # SQL 生成次数（含修复）
    needs_approval: bool     # 是否触发人工审批
    approved: bool           # 人工审批结果
    interrupt_reason: str    # 触发审批的原因
    columns: list[str]       # 查询结果列名
    rows: list[list]         # 查询结果行
    answer: str              # 最终结论
    chart: dict              # 图表配置 {chart_type, x, y, title}
    status: str              # done / failed / rejected / pending_approval
    steps: list[dict]        # 执行轨迹（用于追踪与演示）
