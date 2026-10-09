"""全局配置：环境变量驱动，Pydantic 校验。"""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # LLM
    llm_api_key: str = ""
    llm_api_base: str = "https://api.deepseek.com"
    llm_model: str = "deepseek-chat"
    fallback_model: str = ""          # 主模型连续失败后的降级模型，留空则不降级
    temperature: float = 0.0

    # 容错
    max_retries: int = 2              # 单模型 API 重试次数
    sql_max_attempts: int = 3         # SQL 生成+自修复最大次数
    breaker_failure_threshold: int = 3   # 连续失败 N 次触发熔断
    breaker_cooldown_seconds: int = 30   # 熔断冷却时间
    session_token_budget: int = 200_000  # 进程级 Token 预算保护

    # 安全
    max_rows: int = 200               # 单次查询最大返回行数
    sensitive_columns: list[str] = ["phone", "email", "id_card", "salary", "address"]

    # 路径
    db_path: str = "data/ecommerce.db"
    trace_dir: str = "traces"


@lru_cache
def get_settings() -> Settings:
    return Settings()
