"""LLM 客户端：重试退避 + 模型降级 + 熔断器 + Token 统计。"""
import time
from dataclasses import dataclass

from openai import APIConnectionError, APIError, APITimeoutError, OpenAI, RateLimitError

from chatbi.config import Settings, get_settings

RETRYABLE_ERRORS = (APITimeoutError, RateLimitError, APIConnectionError)


class CircuitOpenError(Exception):
    """熔断器打开期间拒绝调用。"""


@dataclass
class LLMResponse:
    content: str
    prompt_tokens: int
    completion_tokens: int
    model: str


class CircuitBreaker:
    """连续失败 N 次后熔断，冷却后半开试探恢复。"""

    def __init__(self, failure_threshold: int, cooldown_seconds: int):
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self.consecutive_failures = 0
        self.opened_at = 0.0

    def allow(self) -> bool:
        if self.consecutive_failures < self.failure_threshold:
            return True
        return time.time() - self.opened_at >= self.cooldown_seconds

    def record_success(self) -> None:
        self.consecutive_failures = 0

    def record_failure(self) -> None:
        self.consecutive_failures += 1
        if self.consecutive_failures >= self.failure_threshold:
            self.opened_at = time.time()

    @property
    def is_open(self) -> bool:
        return not self.allow()


class LLMClient:
    """OpenAI 兼容接口封装：应用层容错 + 全局 Token 计量。"""

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self._client = OpenAI(api_key=self.settings.llm_api_key, base_url=self.settings.llm_api_base,
                              max_retries=self.settings.max_retries)
        self._breaker = CircuitBreaker(self.settings.breaker_failure_threshold,
                                       self.settings.breaker_cooldown_seconds)
        self.total_prompt_tokens = 0
        self.total_completion_tokens = 0
        self.call_count = 0

    @property
    def total_tokens(self) -> int:
        return self.total_prompt_tokens + self.total_completion_tokens

    def chat(self, messages: list[dict], json_mode: bool = False) -> LLMResponse:
        """调用 LLM：主模型重试 → 降级模型重试 → 全部失败抛异常；熔断打开直接拒绝。"""
        if not self._breaker.allow():
            raise CircuitOpenError("熔断器打开中，模型调用被拒绝")

        candidates = [self.settings.llm_model]
        if self.settings.fallback_model:
            candidates.append(self.settings.fallback_model)

        last_error: Exception | None = None
        for index, model in enumerate(candidates):
            try:
                return self._invoke(messages, model, json_mode)
            except RETRYABLE_ERRORS as error:
                # 可重试错误：退避后重试；重试耗尽换降级模型
                last_error = error
                self._breaker.record_failure()
                time.sleep(2 ** index)
            except APIError as error:
                # 不可重试错误：直接尝试降级模型
                last_error = error
                self._breaker.record_failure()

        raise last_error  # type: ignore[misc]

    def _invoke(self, messages: list[dict], model: str, json_mode: bool) -> LLMResponse:
        kwargs = {"response_format": {"type": "json_object"}} if json_mode else {}
        response = self._client.chat.completions.create(
            model=model, messages=messages,
            temperature=self.settings.temperature, **kwargs,
        )
        usage = response.usage
        self.total_prompt_tokens += usage.prompt_tokens
        self.total_completion_tokens += usage.completion_tokens
        self.call_count += 1
        self._breaker.record_success()
        return LLMResponse(content=response.choices[0].message.content or "",
                           prompt_tokens=usage.prompt_tokens,
                           completion_tokens=usage.completion_tokens,
                           model=model)
