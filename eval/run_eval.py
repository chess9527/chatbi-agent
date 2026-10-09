"""评估脚本：跑评估集，输出执行准确率 / 自修复率 / Token 成本 / 延迟报告。

用法：python eval/run_eval.py [题数上限]
"""
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from chatbi.config import get_settings  # noqa: E402
from chatbi.database import execute_query  # noqa: E402
from chatbi.graph import ChatBIAgent  # noqa: E402


def normalize_rows(rows: list[list]) -> list[tuple]:
    """结果归一化：剔除截断提示行，浮点保留两位，排序后比较（忽略行序）。"""
    def norm(value):
        if isinstance(value, float):
            return round(value, 2)
        return str(value).strip()

    clean = [row for row in rows if not any(isinstance(c, str) and c.startswith("...") for c in row)]
    return sorted(tuple(norm(v) for v in row) for row in clean)


def measure_values(rows: list[list]) -> list[tuple]:
    """提取每行的数值列（指标），用于 Top-K 并列容忍比较。"""
    def norm(value):
        return round(value, 2) if isinstance(value, float) else value

    result = []
    for row in rows:
        numbers = tuple(norm(v) for v in row if isinstance(v, (int, float)))
        if numbers:
            result.append(numbers)
    return sorted(result)


def results_match(gold_rows: list[list], pred_rows: list[list], both_limit: bool) -> bool:
    """执行匹配：完全相等优先；双方带 LIMIT 时退化为指标列多重集比较（并列容忍）。"""
    if normalize_rows(gold_rows) == normalize_rows(pred_rows):
        return True
    return both_limit and measure_values(gold_rows) == measure_values(pred_rows)


def run_one(agent: ChatBIAgent, item: dict) -> dict:
    start = time.perf_counter()
    result = agent.ask(item["question"])
    if result["status"] == "pending_approval":
        # 评估场景自动批准，真实链路仍经过 interrupt
        result = agent.resume(result["thread_id"], True)
    latency_ms = (time.perf_counter() - start) * 1000

    correct = False
    if result["status"] == "done":
        _, gold_rows = execute_query(item["gold_sql"], get_settings().max_rows)
        both_limit = bool(re.search(r"\bLIMIT\s+\d+", item["gold_sql"], re.IGNORECASE)) \
            and bool(re.search(r"\bLIMIT\s+\d+", result.get("sql", ""), re.IGNORECASE))
        correct = results_match(gold_rows, result.get("rows", []), both_limit)

    repaired = any(step["step"] == "repair_sql" for step in result.get("steps", []))
    return {"id": item["id"], "question": item["question"], "status": result["status"],
            "correct": correct, "repaired": repaired,
            "attempts": result.get("attempts", 0), "latency_ms": round(latency_ms),
            "tokens": sum(s.get("prompt_tokens", 0) + s.get("completion_tokens", 0)
                          for s in result.get("steps", []))}


def write_report(results: list[dict], metrics: dict, path: Path) -> None:
    lines = ["# ChatBI Agent 评估报告", "",
             f"- 执行准确率：**{metrics['accuracy']:.1%}**（{metrics['correct']}/{metrics['total']}）",
             f"- 自修复成功率：**{metrics['repair_success']:.1%}**"
             f"（{metrics['repaired_correct']}/{metrics['repaired']} 次修复后答对）",
             f"- 平均生成次数：{metrics['avg_attempts']:.2f}",
             f"- 平均延迟：{metrics['avg_latency']:.0f} ms",
             f"- 平均 Token 消耗：{metrics['avg_tokens']:.0f} / 题", "",
             "| # | 问题 | 状态 | 结果 | 修复 | 尝试 | 延迟ms | Token |",
             "|---|------|------|------|------|------|--------|-------|"]
    for r in results:
        mark = "✅" if r["correct"] else "❌"
        lines.append(f"| {r['id']} | {r['question']} | {r['status']} | {mark} "
                     f"| {'是' if r['repaired'] else '否'} | {r['attempts']} "
                     f"| {r['latency_ms']} | {r['tokens']} |")
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else None
    questions = json.loads((Path(__file__).parent / "questions.json").read_text(encoding="utf-8"))
    if limit:
        questions = questions[:limit]

    agent = ChatBIAgent()
    results = [run_one(agent, item) for item in questions]

    total = len(results)
    repaired_items = [r for r in results if r["repaired"]]
    metrics = {
        "total": total,
        "correct": sum(r["correct"] for r in results),
        "repaired": len(repaired_items),
        "repaired_correct": sum(r["correct"] for r in repaired_items),
        "avg_attempts": sum(r["attempts"] for r in results) / total,
        "avg_latency": sum(r["latency_ms"] for r in results) / total,
        "avg_tokens": sum(r["tokens"] for r in results) / total,
    }
    metrics["accuracy"] = metrics["correct"] / total
    metrics["repair_success"] = (metrics["repaired_correct"] / metrics["repaired"]) if metrics["repaired"] else 1.0

    print(f"\n执行准确率: {metrics['accuracy']:.1%} ({metrics['correct']}/{total})")
    print(f"自修复成功率: {metrics['repair_success']:.1%} ({metrics['repaired_correct']}/{metrics['repaired']})")
    print(f"平均生成次数: {metrics['avg_attempts']:.2f} | 平均延迟: {metrics['avg_latency']:.0f}ms"
          f" | 平均Token: {metrics['avg_tokens']:.0f}/题")

    report_path = Path(__file__).parent / "report.md"
    write_report(results, metrics, report_path)
    print(f"报告已写入: {report_path}")


if __name__ == "__main__":
    main()
