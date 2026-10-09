"""轻量链路追踪：每次会话的执行轨迹落盘为 JSONL，可回放分析。"""
import json
import time
from datetime import datetime
from pathlib import Path

from chatbi.config import get_settings


def save_trace(session_id: str, question: str, status: str, steps: list[dict],
               prompt_tokens: int, completion_tokens: int, latency_ms: float) -> str:
    """把一次完整问答的轨迹追加写入 traces/目录，返回文件路径。"""
    trace_dir = Path(get_settings().trace_dir)
    trace_dir.mkdir(parents=True, exist_ok=True)

    record = {
        "session_id": session_id,
        "time": datetime.now().isoformat(timespec="seconds"),
        "question": question,
        "status": status,
        "latency_ms": round(latency_ms),
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "steps": steps,
    }
    path = trace_dir / f"{datetime.now():%Y%m%d}.jsonl"
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return str(path)
