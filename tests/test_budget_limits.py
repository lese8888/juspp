import unittest

from translation.budget import (
    BudgetExceeded,
    BudgetedClient,
    DeadlineExceeded,
)
from translation.llm import ChatUsage


class FakeClock:
    def __init__(self):
        self.value = 0.0

    def __call__(self):
        return self.value


class FakeClient:
    model = "fake-model"

    def __init__(self, usage=None, error=None):
        self.usage = usage or ChatUsage(input_tokens=1, output_tokens=1)
        self.error = error
        self.calls = 0

    def complete_json(self, *, system_prompt, user_prompt):
        self.calls += 1
        if self.error:
            raise self.error
        return {"ok": True}, self.usage


def guarded(client, clock, *, budget=20.0, minutes=60):
    return BudgetedClient(
        client,
        max_cost_cny=budget,
        price_input_cny_per_m=2.0,
        price_output_cny_per_m=8.0,
        hard_deadline_minutes=minutes,
        max_output_tokens=16000,
        clock=clock,
    )


class BudgetTests(unittest.TestCase):
    def test_success_settles_actual_usage(self):
        clock = FakeClock()
        client = FakeClient(ChatUsage(input_tokens=1000, output_tokens=2000))
        budgeted = guarded(client, clock)
        budgeted.complete_json(system_prompt="system", user_prompt="user")
        snapshot = budgeted.snapshot()
        self.assertEqual(client.calls, 1)
        self.assertEqual(snapshot.model_requests, 1)
        self.assertAlmostEqual(snapshot.spent_cny, 0.018)
        self.assertEqual(snapshot.unsettled_cny, 0)

    def test_worst_case_reservation_blocks_overspend(self):
        clock = FakeClock()
        client = FakeClient()
        budgeted = guarded(client, clock, budget=0.001)
        with self.assertRaises(BudgetExceeded):
            budgeted.complete_json(
                system_prompt="system" * 100,
                user_prompt="user" * 100,
            )
        self.assertEqual(client.calls, 0)
        self.assertTrue(budgeted.snapshot().budget_exceeded)

    def test_failed_request_keeps_conservative_reservation(self):
        clock = FakeClock()
        client = FakeClient(error=RuntimeError("provider failed"))
        budgeted = guarded(client, clock, budget=1.0)
        with self.assertRaises(RuntimeError):
            budgeted.complete_json(system_prompt="system", user_prompt="user")
        snapshot = budgeted.snapshot()
        self.assertGreater(snapshot.unsettled_cny, 0)
        self.assertEqual(snapshot.spent_cny, 0)

    def test_sixty_minute_deadline(self):
        clock = FakeClock()
        budgeted = guarded(FakeClient(), clock)
        clock.value = 60 * 60
        with self.assertRaisesRegex(DeadlineExceeded, "60 分钟"):
            budgeted.complete_json(system_prompt="system", user_prompt="user")

    def test_actual_cost_above_reservation_fails_closed(self):
        clock = FakeClock()
        client = FakeClient(ChatUsage(input_tokens=10**9, output_tokens=10**9))
        budgeted = guarded(client, clock)
        with self.assertRaises(BudgetExceeded):
            budgeted.complete_json(system_prompt="s", user_prompt="u")
        self.assertTrue(budgeted.snapshot().budget_exceeded)


if __name__ == "__main__":
    unittest.main()
