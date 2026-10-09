"""FastAPI 服务：对话问答、人工审批、Schema 查看、成本统计。"""
import time

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from chatbi.config import get_settings
from chatbi.database import fetch_schema
from chatbi.graph import ChatBIAgent
from chatbi.trace import save_trace

app = FastAPI(title="ChatBI 数据分析 Agent API", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

_agent: ChatBIAgent | None = None


def get_agent() -> ChatBIAgent:
    global _agent
    if _agent is None:
        _agent = ChatBIAgent()
    return _agent


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=500)


class ApproveRequest(BaseModel):
    thread_id: str
    approved: bool


def _token_sums(steps: list[dict]) -> tuple[int, int]:
    prompt = sum(s.get("prompt_tokens", 0) for s in steps)
    completion = sum(s.get("completion_tokens", 0) for s in steps)
    return prompt, completion


def _respond(result: dict, question: str, latency_ms: float) -> dict:
    steps = result.get("steps", [])
    prompt_tokens, completion_tokens = _token_sums(steps)
    save_trace(result["thread_id"], question, result["status"], steps,
               prompt_tokens, completion_tokens, latency_ms)
    return {
        "thread_id": result["thread_id"],
        "status": result["status"],
        "answer": result.get("answer", ""),
        "sql": result.get("sql", ""),
        "columns": result.get("columns", []),
        "rows": result.get("rows", []),
        "chart": result.get("chart", {}),
        "attempts": result.get("attempts", 0),
        "steps": steps,
        "interrupt": result.get("interrupt"),
    }


@app.post("/api/chat")
def chat(request: ChatRequest) -> dict:
    start = time.perf_counter()
    result = get_agent().ask(request.question.strip())
    return _respond(result, request.question, (time.perf_counter() - start) * 1000)


@app.post("/api/approve")
def approve(request: ApproveRequest) -> dict:
    try:
        result = get_agent().resume(request.thread_id, request.approved)
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return _respond(result, result.get("question", ""), 0.0)


@app.get("/api/schema")
def schema() -> dict:
    return {"schema": fetch_schema()}


@app.get("/api/cost")
def cost() -> dict:
    llm = get_agent().llm
    return {"call_count": llm.call_count,
            "prompt_tokens": llm.total_prompt_tokens,
            "completion_tokens": llm.total_completion_tokens,
            "total_tokens": llm.total_tokens,
            "budget": get_settings().session_token_budget}


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}
