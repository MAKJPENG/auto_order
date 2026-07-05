from __future__ import annotations

import unittest
from collections import Counter
from dataclasses import replace
from datetime import datetime, time

from order_bot.models import Order
from order_bot.scheduler import build_schedule
from order_bot.time_utils import get_timezone


class SchedulerTests(unittest.TestCase):
    def setUp(self):
        self.tz = get_timezone("Asia/Shanghai")

    def make_order(self, index: int, run_at=None) -> Order:
        return Order(
            order_id=f"order-{index}",
            run_at=run_at,
            email="buyer@example.com",
            product_url="https://example.com/product",
            quantity=1,
            full_name="Test Buyer",
            country="United Kingdom",
            address_line="1 Test Street",
            city="Birmingham",
            postal_code="B1 1BA",
            payment_method="bank_transfer",
            notes="",
            raw={},
        )

    def test_random_schedule_uses_order_timezone(self):
        order = replace(self.make_order(1), time_zone="Europe/London")

        entries = build_schedule(
            [order],
            spread_days=1,
            tz=self.tz,
            now=datetime(2026, 1, 5, 15, 0, tzinfo=self.tz),
            window_start=time(9, 0),
            window_end=time(9, 5),
            seed=1,
        )

        self.assertEqual(str(entries[0].scheduled_at.tzinfo), "Europe/London")
        self.assertEqual(entries[0].scheduled_at.hour, 9)

    def test_random_orders_are_evenly_spread(self):
        orders = [self.make_order(index) for index in range(6)]
        entries = build_schedule(
            orders,
            spread_days=3,
            tz=self.tz,
            now=datetime(2026, 7, 5, 8, 0, tzinfo=self.tz),
            window_start=time(9, 0),
            window_end=time(18, 0),
            seed=7,
        )

        counts = Counter(entry.scheduled_at.date() for entry in entries)
        self.assertEqual(sorted(counts.values()), [2, 2, 2])

        clock_times = [entry.scheduled_at.strftime("%H:%M:%S") for entry in entries]
        self.assertEqual(len(clock_times), len(set(clock_times)))

    def test_run_at_is_preserved(self):
        run_at = datetime(2026, 7, 6, 16, 45, tzinfo=self.tz)
        entries = build_schedule(
            [self.make_order(1, run_at=run_at), self.make_order(2)],
            spread_days=3,
            tz=self.tz,
            now=datetime(2026, 7, 5, 8, 0, tzinfo=self.tz),
            seed=3,
        )

        fixed = next(entry for entry in entries if entry.order.order_id == "order-1")
        self.assertEqual(fixed.scheduled_at, run_at)
        self.assertEqual(fixed.source, "run_at")


if __name__ == "__main__":
    unittest.main()
