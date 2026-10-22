# 📊 ChatBI 数据分析 Agent

自然语言直接问数据库：LLM 生成 SQL → 安全校验 → 报错自修复 → 敏感查询人工审批 → 结论 + 可视化图表。

一个面向生产环境的 Text-to-SQL Agent，重点不是"能跑"，而是**可控、可审计、可量化**。

## ✨ 核心特性

| 能力 | 实现 |
|------|------|
| 🔁 **SQL 自修复循环** | 执行/校验报错自动回喂 LLM 修复，最多 3 次尝试 |
| 🛡️ **SQL 安全校验** | 只放行单条只读 SELECT、禁用关键词逐 token 检查、强制行数上限、连接层 `mode=ro` 双保险 |
| 🧑‍💼 **Human-in-the-Loop** | 基于 LangGraph `interrupt`：敏感字段查询 / 低置信度 SQL 暂停挂起，人工批准后才执行 |
| 🔌 **生产容错** | API 重试 + 指数退避、备用模型自动降级、熔断器（连续失败熔断冷却）、Token 预算保护 |
| 💰 **成本可观测** | 每次调用 Token 计量、成本看板、JSONL 全链路轨迹落盘可回放 |
| 📏 **量化评估** | 内置 20 题评估集，产出执行准确率 / 自修复成功率 / 平均延迟 / Token 成本报告 |

## 🏗️ 架构

```
用户问题
   │
   ▼
┌──────────────────────── LangGraph 状态图 ────────────────────────┐
│                                                                  │
│  generate_sql ──► validate_sql ──┬─(涉及敏感列/低置信度)─► approval │
│      ▲                           │                         │     │
│      │                     (校验失败)                      (批准/拒绝)│
│      │                           ▼                         ▼     │
│   repair_sql ◄──── execute_sql ──┴─(执行报错)──► repair_sql  summarize │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
   │              │                │
 Streamlit UI   FastAPI API    SQLite(只读)
 成本看板/审批    轨迹JSONL落盘   schema注入Prompt
```

## 🛠️ 技术栈

Python 3.11+ · LangGraph（StateGraph + interrupt 人工介入）· DeepSeek（OpenAI 兼容）· FastAPI · Streamlit · SQLite · sqlparse

## 🚀 快速开始

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 配置 Key
cp .env.example .env   # 填入 LLM_API_KEY

# 3. 生成示例电商数据库（用户/商品/订单/订单明细，约 400 单）
python scripts/init_db.py

# 4. 启动后端
uvicorn api:app --port 8000

# 5. 启动界面（新终端）
streamlit run app.py
```

Docker 方式：先 `cp .env.example .env` 填好 Key，再 `docker compose up --build`

## 📏 跑评估

```bash
python eval/run_eval.py        # 全量 20 题
python eval/run_eval.py 5      # 前 5 题快速验证
```

输出执行准确率、自修复成功率、平均延迟、平均 Token 消耗，报告写入 `eval/report.md`。

实测结果（deepseek-chat，20 题）：**执行准确率 95%（19/20）**，平均延迟 2.5s，平均 Token 约 1276/题。

## 📁 项目结构

```
chatbi-agent/
├── app.py                 # Streamlit 界面（对话/审批/图表/成本看板）
├── api.py                 # FastAPI（chat / approve / schema / cost）
├── chatbi/
│   ├── graph.py           # LangGraph 编排（自修复循环 + interrupt 审批）
│   ├── llm.py             # LLM 客户端（重试退避/模型降级/熔断器/Token统计）
│   ├── security.py        # SQL 安全校验（只读/多语句/关键词/行数/敏感列）
│   ├── database.py        # SQLite 只读访问 + Schema 提取
│   ├── state.py           # AgentState 状态定义
│   ├── trace.py           # JSONL 链路追踪
│   └── config.py          # Pydantic Settings 配置
├── eval/
│   ├── questions.json     # 20 题评估集（含金标 SQL）
│   └── run_eval.py        # 跑分脚本 → report.md
├── scripts/init_db.py     # 示例电商数据库生成（确定性随机）
├── Dockerfile / docker-compose.yml
└── requirements.txt
```

##  License

MIT
