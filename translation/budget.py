"""模型调用预算和硬截止保护。"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

from .llm import ChatUsage


class BudgetExceeded(RuntimeError):
    """下一次最坏请求成本会超过已批准预算。"""


class DeadlineExceeded(RuntimeError):
    """影子任务已经达到硬截止时间。"""


@dataclass(frozen=True)
class BudgetSnapshot:
    """可写入报告的预算账本。"""

    max_cost_cny: float
    spent_cny: float
    unsettled_cny: float
    remaining_cny: float
    model_requests: int
    budget_exceeded: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "maxCostCny": round(self.max_cost_cny, 6),
            "spentCny": round(self.spent_cny, 6),
            "unsettledCny": round(self.unsettled_cny, 6),
            "remainingCny": round(self.remaining_cny, 6),
            "modelRequests": self.model_requests,
            "budgetExceeded": self.budget_exceeded,
        }


class BudgetedClient:
    """为模型客户端增加并发安全的最坏成本预留与截止检查。"""

    def __init__(
        self,
        client: Any,
        *,
        max_cost_cny: float,
        price_input_cny_per_m: float,
        price_output_cny_per_m: float,
        hard_deadline_minutes: float,
        max_output_tokens: int = 16000,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if max_cost_cny <= 0:
            raise ValueError("max_cost_cny 必须大于 0")
        if hard_deadline_minutes <= 0:
            raise ValueError("hard_deadline_minutes 必须大于 0")
        if max_output_tokens <= 0:
            raise ValueError("max_output_tokens 必须大于 0")
        self._client = client
        self._max_cost_cny = float(max_cost_cny)
        self._price_input = float(price_input_cny_per_m)
        self._price_output = float(price_output_cny_per_m)
        self._max_output_tokens = int(max_output_tokens)
        self._clock = clock
        self._started_at = clock()
        self._deadline_at = self._started_at + (
            float(hard_deadline_minutes) * 60
        )
        self._spent_cny = 0.0
        self._unsettled_cny = 0.0
        self._requests = 0
        self._budget_exceeded = False
        self._lock = threading.RLock()

    @property
    def model(self) -> str:
        return str(getattr(self._client, "model", "") or "")

    def complete_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
    ) -> tuple[dict[str, Any], ChatUsage]:
        """在请求前预留最坏成本，成功后按实际 usage 结算。"""

        self._check_deadline()
        # 对 token 数采用保守上界：按完整提示字符数计输入，按客户端
        # max_tokens 计输出。它可能高估，但不会低估单次请求的最坏成本。
        input_token_upper_bound = max(
            1,
            len(str(system_prompt or "")) + len(str(user_prompt or "")),
        )
        reservation = self._cost(
            input_tokens=input_token_upper_bound,
            output_tokens=self._max_output_tokens,
        )

        with self._lock:
            committed = self._spent_cny + self._unsettled_cny
            if committed + reservation > self._max_cost_cny + 1e-12:
                self._budget_exceeded = True
                raise BudgetExceeded(
                    "模型请求预算不足以覆盖下一次最坏成本："
                    f"上限 ¥{self._max_cost_cny:.6f}，"
                    f"已消耗 ¥{self._spent_cny:.6f}，"
                    f"未结算预留 ¥{self._unsettled_cny:.6f}，"
                    f"本次预留 ¥{reservation:.6f}"
                )
            self._unsettled_cny += reservation
            self._requests += 1

        try:
            result, usage = self._client.complete_json(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
            )
        except Exception:
            # 失败请求也可能已经被供应商计费；保留预留额，宁可提前停止，
            # 也不让无法核实的失败请求突破成本上限。
            self._check_deadline()
            raise

        actual_cost = self._cost(
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
        )
        with self._lock:
            self._unsettled_cny = max(
                0.0,
                self._unsettled_cny - reservation,
            )
            self._spent_cny += actual_cost
            if self._spent_cny > self._max_cost_cny + 1e-12:
                self._budget_exceeded = True
        if actual_cost > reservation:
            raise BudgetExceeded(
                "模型实际用量超过请求前预留成本："
                f"预留 ¥{reservation:.6f}，实际 ¥{actual_cost:.6f}"
            )
        return result, usage

    def snapshot(self) -> BudgetSnapshot:
        """返回当前预算账本。"""

        with self._lock:
            return BudgetSnapshot(
                max_cost_cny=self._max_cost_cny,
                spent_cny=self._spent_cny,
                unsettled_cny=self._unsettled_cny,
                remaining_cny=max(
                    0.0,
                    self._max_cost_cny
                    - self._spent_cny
                    - self._unsettled_cny,
                ),
                model_requests=self._requests,
                budget_exceeded=self._budget_exceeded,
            )

    def _check_deadline(self) -> None:
        now = self._clock()
        if now >= self._deadline_at:
            raise DeadlineExceeded("影子任务已经达到 60 分钟硬截止")

    def _cost(self, *, input_tokens: int, output_tokens: int) -> float:
        return (
            max(0, int(input_tokens)) * self._price_input
            + max(0, int(output_tokens)) * self._price_output
        ) / 1_000_000
