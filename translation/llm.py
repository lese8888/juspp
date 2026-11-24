"""OpenAI 兼容的 LLM 客户端，供影子任务使用。"""

from __future__ import annotations

import json
import random
import re
import time
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class LLMConfigurationError(RuntimeError):
    """模型配置缺失或不合法。"""


class LLMRequestError(RuntimeError):
    """模型请求失败。"""

    def __init__(self, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = retryable


@dataclass(frozen=True)
class ChatUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    estimated: bool = False

    def __add__(self, other: "ChatUsage") -> "ChatUsage":
        return ChatUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            estimated=self.estimated or other.estimated,
        )


def _estimated_usage(prompt: str, output: str) -> ChatUsage:
    # 只用于供应商未返回 usage 时的保守估算。
    return ChatUsage(
        input_tokens=max(1, len(prompt) // 3),
        output_tokens=max(1, len(output) // 2),
        estimated=True,
    )


def _strip_json_fence(value: str) -> str:
    text = str(value or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


class OpenAICompatibleClient:
    """调用 OpenAI 兼容的 `/chat/completions` 接口。"""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        timeout_seconds: float = 120,
        max_retries: int = 3,
        json_mode: bool = True,
        max_tokens: int = 1600,
        temperature: float = 0.2,
    ) -> None:
        if not api_key.strip():
            raise LLMConfigurationError("缺少 LLM_API_KEY，无法启动真实模型影子任务。")
        if not base_url.strip() or not model.strip():
            raise LLMConfigurationError("LLM_BASE_URL 和 LLM_MODEL 不能为空。")
        self._api_key = api_key.strip()
        self._base_url = base_url.rstrip("/")
        self.model = model.strip()
        self.timeout_seconds = timeout_seconds
        self.max_retries = max(1, max_retries)
        self.json_mode = json_mode
        self.max_tokens = max_tokens
        self.temperature = temperature

    def complete_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
    ) -> tuple[dict[str, Any], ChatUsage]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        if self.json_mode:
            payload["response_format"] = {"type": "json_object"}

        for parse_attempt in range(2):
            content, actual_usage = self._request(payload)
            normalized = _strip_json_fence(content)
            try:
                result = json.loads(normalized)
            except json.JSONDecodeError as error:
                if parse_attempt == 0:
                    time.sleep(1)
                    continue
                raise LLMRequestError(
                    f"模型没有返回有效 JSON：{normalized[:200]}",
                    retryable=True,
                ) from error
            if not isinstance(result, dict):
                raise LLMRequestError(
                    "模型返回的 JSON 顶层不是对象。",
                    retryable=True,
                )
            return result, actual_usage or _estimated_usage(
                f"{system_prompt}\n{user_prompt}",
                content,
            )
        raise LLMRequestError("模型未返回有效 JSON。", retryable=True)

    def _request(self, payload: dict[str, Any]) -> tuple[str, ChatUsage | None]:
        endpoint = f"{self._base_url}/chat/completions"
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        last_error: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            request = Request(
                endpoint,
                data=body,
                method="POST",
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "User-Agent": "YouwenShadow/1.0",
                },
            )
            try:
                with urlopen(request, timeout=self.timeout_seconds) as response:
                    result = json.loads(response.read().decode("utf-8"))
                choices = result.get("choices") or []
                if not choices:
                    raise LLMRequestError("模型响应缺少 choices。", retryable=True)
                choice = choices[0]
                message = choice.get("message") or {}
                content = message.get("content")
                if not isinstance(content, str) or not content.strip():
                    finish_reason = choice.get("finish_reason") or "unknown"
                    reasoning = (
                        message.get("reasoning_content")
                        or message.get("reasoning")
                        or ""
                    )
                    raise LLMRequestError(
                        "模型响应内容为空"
                        f"（finish_reason={finish_reason}，"
                        f"reasoning_chars={len(str(reasoning))}）。",
                        retryable=True,
                    )
                usage_payload = result.get("usage") or {}
                usage = None
                if usage_payload:
                    usage = ChatUsage(
                        input_tokens=int(usage_payload.get("prompt_tokens") or 0),
                        output_tokens=int(usage_payload.get("completion_tokens") or 0),
                    )
                return content, usage
            except HTTPError as error:
                detail = error.read().decode("utf-8", errors="replace")[:500]
                retryable = error.code == 429 or 500 <= error.code < 600
                last_error = LLMRequestError(
                    f"模型接口 HTTP {error.code}：{detail}",
                    retryable=retryable,
                )
                if not retryable:
                    raise last_error from error
            except (TimeoutError, URLError, OSError, json.JSONDecodeError) as error:
                last_error = LLMRequestError(
                    f"模型请求失败：{type(error).__name__}：{error}",
                    retryable=True,
                )
            if attempt < self.max_retries:
                delay = (2 ** (attempt - 1)) + random.uniform(0.1, 0.5)
                time.sleep(delay)
        raise LLMRequestError(
            str(last_error or "模型请求失败。"),
            retryable=True,
        )
